//[2026-05-21 Hadi]test code Metal 3D Printer Board (RP2350B 80 pins MCU)
//This code send a rectangle on the output to test
// The X,Y,SYNC, and CLK pins are updated according to the new board (GPIO 33–36)
//The PIO0 changed to PIO2
// The line "pio->gpiobase = 0x10;" has been addded to map the address pins of the PIO for RP2350B
//[2026-07-16] Added relay functionality: RELAY1 (GPIO19) and RELAY2 (GPIO20) toggle every 5 seconds
#include <stdio.h>
#include <math.h>
#include "pico/stdlib.h"
#include "hardware/pio.h"
#include "hardware/clocks.h"
#include "hardware/uart.h"
#include "json_api.h"
#include "layer.h"
#include "gcode.h"
#include "hardware/pwm.h"
#include "xy2_100.pio.h"   // NEW: generated from modified PIO file with .side_set 2


// #define PIN_XDATA 2
// #define PIN_YDATA 3
// #define PIN_CLK   4   // first side-set pin
// #define PIN_SYNC  5   // second side-set pin (next GPIO after PIN_CLK)
// #define LED_PIN   25  // onboard LED

//[2026-05-21] The following modification used for RP2350B(80 pins) CPU on physical pins (GPIO 33–36)
#define PIN_XDATA 33 //for CPU-C of Metal 3D Printer Board
#define PIN_YDATA 34 //for CPU-C of Metal 3D Printer Board
#define PIN_CLK   35 //for CPU-C of Metal 3D Printer Board
#define PIN_SYNC  36 //for CPU-C of Metal 3D Printer Board   
#define LED_PIN   2  //onboard LED for CPU-C of Metal 3D Printer Board

//[2026-11-27] Link to CPU-B. UART0 on GP0 (TX) and GP1 (RX), reaching CPU-B's GP5 (RX) and
// GP4 (TX) through the 10R series resistors R90/R91. Mirrors the CPU-B <-> CPU-A link.
#define CPUB_UART        uart0
#define CPUB_UART_TX_PIN 0
#define CPUB_UART_RX_PIN 1
// 2 Mbaud; must match CPUC_BAUD_RATE in the CPU-B firmware.
// NOTE: at this rate the 32 byte hardware RX FIFO fills in ~160 us, so cpub_link_poll()
// has to keep being called from the inner streaming loop (it runs every ~5 us there).
// Anything that blocks longer than 160 us -- a chatty printf to USB CDC with debuggingBase
// on, for instance -- can drop bytes. An IRQ driven ring buffer would remove that risk.
#define CPUB_BAUD_RATE   2000000

//[2026-11-27] Relays driven by CPU-C, named after the nets on the V2 schematic.
//
// Galvo supply. The scan head needs BOTH rails, so the two are driven together as one
// "galvo power" control: energising +15 V without -15 V would feed the head an asymmetric
// supply. They are separate nets on the board, hence two pins.
#define GALVO_NEG_RLY_PIN      19   // PIO_15V_RLY  -> K7, -15 V to the scan head
#define GALVO_POS_RLY_PIN      20   // PIO_PWR_RLY  -> K6, +15 V to the scan head

// K6/K7 are driven ACTIVE-LOW: the GPIO pulls the TPM40V gate down to close the
// relay, so a LOW pin energises the coil and feeds the head. Established on the
// bench -- the head was live whenever the firmware believed it was off, and the
// board came up with the rails already on because every relay pin is driven low
// at boot. Written as a level rather than inline so the reported galvoPower
// keeps meaning "head powered" whichever way the hardware is wired.
#define GALVO_RLY_ACTIVE       0
#define GALVO_RLY_INACTIVE     1

// Raycus RFL-C200A control relays. These are individual relay controls, NOT a power-up
// sequencer: the vendor's required order is interlock closed, then main power, then remote
// on, and honouring that is the caller's job.
#define LASER_INTERLOCK_RLY_PIN  24   // Interlock_RLY  -> the DB25 interlock pin pairs
#define LASER_MAIN_POWER_RLY_PIN 26   // LaserPowerON   -> REMOTE_PWRON
#define LASER_REMOTE_ON_RLY_PIN  25   // LaserRemoteON  -> REMOTE_ON

// Beam control, driven per point by the G-code interpreter.
//   Laser_PWM goes to the LM358 PWM-to-0..10 V converter, so it sets POWER.
//   Laser_MOD+ is the RFL-C200A's TTL modulation input, so it is the GATE.
// Both are harmless with only a red pointer attached, and this is the same code path the
// fiber laser will take, so it is worth exercising from the start.
#define LASER_PWM_PIN  21   // Laser_PWM  -> power
#define LASER_MOD_PIN  22   // Laser_MOD+ -> on/off gate
#define LASER_PWM_WRAP 1000 // duty resolution; ~20 kHz so the converter's filter can smooth it

// --- Configurable galvo limits (mm to 16-bit mapping) ---
#define X_MAX_POS_MM   250.0
#define Y_MAX_POS_MM   250.0
#define AXIS_INVERSE_X 0
#define AXIS_INVERSE_Y 0
#define delaygalvo 5 //us
//1730 // delay in ms between sending X and Y frames
//#define NUM_POINTS 16000   // total points for the square
#define PI 3.14159265358979323846
#define SHAPE_CIRCLE     0
#define SHAPE_RECTANGLE  1
#define STEP_SIZE_MM  0.05       // point spacing
#define MAX_POINTS    20000      // max buffer size (safe upper bound)


// Function to linearly interpolate (optional, not needed for circle)
double lerp(double a, double b, double t) { 
    return a + t * (b - a); 
}

// Map float [in_min..in_max] → int [out_min..out_max]
static inline int map_double(double val, double in_min, double in_max, int out_min, int out_max)
{
    return (int)((val - in_min) * (double)(out_max - out_min) / (double)(in_max - in_min) + out_min);
}

// --- Pack XY2-100 frame (C0,C1,C2 + pos in mm) ---
uint32_t pack_xy2_frame(uint8_t c0, uint8_t c1, uint8_t c2,
                        double pos_mm, double axis_max_mm, bool inverse)
{
    // Scale mm → 16-bit range (0..65535)
    int tmp;
    if(inverse)
        tmp = map_double(pos_mm, 0.0, axis_max_mm, 65535, 0);
    else
        tmp = map_double(pos_mm, 0.0, axis_max_mm, 0, 65535);

    // Frame format: [C2 C1 C0 DATA(16) PARITY]
    uint32_t frame = 0;
    c0 &= 1; c1 &= 1; c2 &= 1;

    frame |= (c2 << 19);
    frame |= (c1 << 18);
    frame |= (c0 << 17);
    frame |= ((uint16_t)tmp & 0xFFFF) << 1;

    // Even parity over 19 bits
    uint32_t tmp_frame = frame;
    int ones = 0;
    for (int i = 0; i < 19; i++)
        ones += (tmp_frame >> i) & 1;
    uint8_t parity = ones & 1;
    frame |= parity;

    // Align to top of 32 bits (autopull=20)
    frame <<= (32 - 20);

    return frame;
}

// Convenience wrappers for X and Y
inline uint32_t pack_x_frame(double x_mm, uint8_t c0=1, uint8_t c1=0, uint8_t c2=0) {
    return pack_xy2_frame(c0,c1,c2, x_mm, X_MAX_POS_MM, AXIS_INVERSE_X);
}
inline uint32_t pack_y_frame(double y_mm, uint8_t c0=1, uint8_t c1=0, uint8_t c2=0) {
    return pack_xy2_frame(c0,c1,c2, y_mm, Y_MAX_POS_MM, AXIS_INVERSE_Y);
}


// // --- Pack XY2-100 frame (20 bits) ---
// // input: control bits (C0,C1,C2), 16-bit signed position
// // output: 20-bit word, MSB aligned in 32 bits for PIO (autopull=20)
// uint32_t pack_xy2_frame(uint8_t c0, uint8_t c1, uint8_t c2, int16_t position)
// {
//     // Frame format: [C2 C1 C0 DATA(16) PARITY]
//     uint32_t frame = 0;

//     // mask control bits to 1-bit values
//     c0 &= 1;
//     c1 &= 1;
//     c2 &= 1;

//     // shift control bits into top of frame
//     frame |= (c2 << 19);  
//     frame |= (c1 << 18);
//     frame |= (c0 << 17);

//     // insert 16-bit signed position
//     frame |= ((uint16_t)position & 0xFFFF) << 1;

//     // calculate parity bit (even parity over previous 19 bits)
//     uint32_t tmp = frame;
//     int ones = 0;
//     for (int i = 0; i < 19; i++) {
//         ones += (tmp >> i) & 1;
//     }
//     uint8_t parity = ones & 1;   // if odd, parity=1; if even, parity=0
//     frame |= parity;             // put at bit0

//     // Now align 20 bits into top of 32 bits (for autopull=20)
//     frame <<= (32 - 20);

//     return frame;
// }

// Convenience wrappers for X and Y
// inline uint32_t make_x_frame(int16_t x) { return pack_xy2_frame(0,0,0,x); }
// inline uint32_t make_y_frame(int16_t y) { return pack_xy2_frame(0,0,0,y); }

    // // Arrays to hold precomputed path frames
    // uint32_t x_frames[NUM_POINTS];
    // uint32_t y_frames[NUM_POINTS];
    // // Example: square path coordinates in mm
    // double x_coords[4] = {50, 100, 100,  50};
    // double y_coords[4] = {50,  50, 100, 100};

    // // Linear interpolation helper
    // double lerp(double a, double b, double t) {
    //     return a + (b - a) * t;
    // }



// --- CPU-B link -------------------------------------------------------------------------
// Transport only: bytes are forwarded verbatim in both directions, with no framing or
// parsing, because the CPU-B <-> CPU-C protocol is not decided yet.

static void cpub_link_init(void)
{
    uart_init(CPUB_UART, CPUB_BAUD_RATE);
    gpio_set_function(CPUB_UART_TX_PIN, GPIO_FUNC_UART);
    gpio_set_function(CPUB_UART_RX_PIN, GPIO_FUNC_UART);
    uart_set_hw_flow(CPUB_UART, false, false);
    uart_set_format(CPUB_UART, 8, 1, UART_PARITY_NONE);
    uart_set_fifo_enabled(CPUB_UART, true);
}

static void cpub_link_write(const char *text)
{
    uart_puts(CPUB_UART, text);
}

static void usb_write(const char *text)
{
    fputs(text, stdout);
    fflush(stdout);
}

// Drains both command transports into the JSON API.
// Call this often: the UART FIFO is only 32 bytes deep, and at 115200 baud it fills in
// under 3 ms, so polling once per galvo sweep would drop bytes.
static void api_poll(void)
{
    while (uart_is_readable(CPUB_UART))
        json_api_feed(API_SRC_CPUB, (char)uart_getc(CPUB_UART));

    int c;
    while ((c = getchar_timeout_us(0)) != PICO_ERROR_TIMEOUT)
        json_api_feed(API_SRC_USB, (char)c);
}

static void laser_init(void)
{
    gpio_set_function(LASER_PWM_PIN, GPIO_FUNC_PWM);
    pwm_config cfg = pwm_get_default_config();
    pwm_config_set_clkdiv(&cfg, 7.5f);               // ~20 kHz at wrap 1000
    pwm_config_set_wrap(&cfg, LASER_PWM_WRAP - 1);
    pwm_init(pwm_gpio_to_slice_num(LASER_PWM_PIN), &cfg, true);
    pwm_set_gpio_level(LASER_PWM_PIN, 0);

    gpio_init(LASER_MOD_PIN);
    gpio_set_dir(LASER_MOD_PIN, true);
    gpio_put(LASER_MOD_PIN, 0);                      // gated off
}

static void laser_apply(bool on, float power)
{
    if (power < 0.0f) power = 0.0f;
    if (power > 1.0f) power = 1.0f;
    pwm_set_gpio_level(LASER_PWM_PIN, (uint16_t)(power * (LASER_PWM_WRAP - 1)));
    gpio_put(LASER_MOD_PIN, on);
}

// Feeds the PIO from the interpreter. Paced by the TX FIFO rather than a delay: push while
// there is room and stop when there is not, so the state machines set the rate and the loop
// simply keeps up. This is also the shape DMA will take over later.
static void run_gcode_frames(PIO pio, uint sm_x, uint sm_y)
{
    while (!pio_sm_is_tx_fifo_full(pio, sm_x) && !pio_sm_is_tx_fifo_full(pio, sm_y)) {

        gcode_point_t gp;
        const gcode_status_t st = gcode_next_point(&gp);

        if (st == GCODE_POINT) {
            pio_sm_put(pio, sm_x, pack_x_frame(gp.x_mm));
            pio_sm_put(pio, sm_y, pack_y_frame(gp.y_mm));
            laser_apply(gp.laser_on, gp.power);
            continue;
        }

        // Anything other than a point ends the run. Gate the beam off before saying so:
        // the recoater sweeps the build area as soon as CPU-B hears this.
        laser_apply(false, 0.0f);

        // The speeds are reported with every completed run on purpose. They live in RAM and
        // survive until CPU-C reboots, so a run started today can still be using speeds some
        // earlier session set -- which makes a timing measured now impossible to reproduce or
        // model unless the run says what it actually used. An F word in the file overrides
        // markSpeed per move, so this is the configured speed, not necessarily the one used.
        char speeds[128];
        snprintf(speeds, sizeof(speeds),
                 ",\"markSpeed\":%.0f,\"jumpSpeed\":%.0f"
                 ",\"jumpDelay\":%u,\"markDelay\":%u,\"laserOnDelay\":%u",
                 (double)gcode_config.mark_speed_mm_s, (double)gcode_config.jump_speed_mm_s,
                 gcode_config.jump_delay_frames, gcode_config.mark_delay_frames,
                 gcode_config.laser_on_delay_frames);

        char body[256];
        if (st == GCODE_LAYER_END) {
            layer_mark_paused();          // partly consumed: awaiting recoat and a resume
            snprintf(body, sizeof(body),
                     "\"event\":\"layerComplete\",\"layer\":%u,\"z\":%.3f%s",
                     (unsigned)gcode_layer_index(), (double)gcode_layer_thickness_mm(),
                     speeds);
        } else {
            layer_mark_done();
            snprintf(body, sizeof(body), "\"event\":\"programComplete\",\"layer\":%u%s",
                     (unsigned)gcode_layer_index(), speeds);
        }
        json_api_emit(body);
        break;
    }
}

int main() {
    stdio_init_all();

    // sleep_ms(2000);     // wait for terminal to open
    // printf("XY2-100 Protocol with PICO2 PIO...\n");



    //PIO pio = pio0;
    //[2026-05-21] The following modification used for RP2350B(80 pins) CPU
    PIO pio = pio2;// 2026-05-21
    //re-aligned the entire 32-bit internal register bus of PIO2 to look exactly at physical pins 16 through 47.
    pio->gpiobase = 0x10; // FORCES PIO2 to view the High Bank (0x10 = 16)
    uint sm_x = 0;
    uint sm_y = 1;

    // Init LED
    gpio_init(LED_PIN);
    gpio_set_dir(LED_PIN, true);

    //[2026-11-27] Bring up the CPU-B link and the JSON API before the galvo starts
    cpub_link_init();
    laser_init();
    layer_init();
    json_api_init(usb_write, cpub_link_write);
    api_state.galvo_max_x_mm = X_MAX_POS_MM;
    api_state.galvo_max_y_mm = Y_MAX_POS_MM;
    cpub_link_write("{\"from\":\"cpuc\",\"event\":\"linkUp\"}\r\n");

    //[2026-11-27] Every relay starts driven to its INACTIVE level, so nothing is energised
    // by merely powering the board and no coil is left floating. The galvo pair is
    // active-low (see GALVO_RLY_ACTIVE), so for those that level is HIGH -- driving them
    // low here is what used to bring the scan head up with the board.
    static const uint laser_relay_pins[] = {
        LASER_INTERLOCK_RLY_PIN, LASER_MAIN_POWER_RLY_PIN, LASER_REMOTE_ON_RLY_PIN,
    };
    for (size_t i = 0; i < sizeof(laser_relay_pins) / sizeof(laser_relay_pins[0]); i++) {
        gpio_init(laser_relay_pins[i]);
        gpio_set_dir(laser_relay_pins[i], true);
        gpio_put(laser_relay_pins[i], 0);
    }
    static const uint galvo_relay_pins[] = { GALVO_NEG_RLY_PIN, GALVO_POS_RLY_PIN };
    for (size_t i = 0; i < sizeof(galvo_relay_pins) / sizeof(galvo_relay_pins[0]); i++) {
        gpio_init(galvo_relay_pins[i]);
        gpio_set_dir(galvo_relay_pins[i], true);
        gpio_put(galvo_relay_pins[i], GALVO_RLY_INACTIVE);
    }

    // Load programs
    uint offset_master = pio_add_program(pio, &xy2_100_program); // X+CLK+SYNC via side-set
    uint offset_slave  = pio_add_program(pio, &xy2_slave_program);        // Y only

    // SM0: X data + clock 
    pio_sm_config c0 = xy2_100_program_get_default_config(offset_master);
    sm_config_set_out_pins(&c0, PIN_XDATA, 1);     // XDATA pin
    sm_config_set_sideset_pins(&c0, PIN_CLK);      // first side-set pin = CLK,
    //sm_config_set_out_shift(&c0, false, true, 20); // MSB-first, autopull 20 bits

    // SM1: Y data + sync 
    pio_sm_config c1 = xy2_slave_program_get_default_config(offset_slave);
    sm_config_set_out_pins(&c1, PIN_YDATA, 1);
    sm_config_set_sideset_pins(&c1, PIN_SYNC);     // first side-set pin = SYNC
    //sm_config_set_in_pins(&c1, PIN_CLK);
    

    // Shift right (LSB first), no autopull (we manage manually)
    sm_config_set_out_shift(&c0, false, false, 32);//flase:shift left

    // Bit clock: 4 instructions per bit → 2 MHz CLK
    //float div = (float)clock_get_hz(clk_sys) / 8'000'000;
    float div = (float)clock_get_hz(clk_sys) / 32'000'000;
    sm_config_set_clkdiv(&c0, div);
   

    // Init pins for SM0
    pio_gpio_init(pio, PIN_XDATA);
    pio_gpio_init(pio, PIN_CLK);
    //pio_gpio_init(pio, PIN_SYNC);
    pio_sm_set_consecutive_pindirs(pio, sm_x, PIN_XDATA, 1, true); // XDATA
    pio_sm_set_consecutive_pindirs(pio, sm_x, PIN_CLK,   1, true); // CLK
    pio_sm_init(pio, sm_x, offset_master, &c0);

    // Shift right (LSB first), no autopull (we manage manually)
    sm_config_set_out_shift(&c1, false, false, 32);//false: shift left
    //sm_config_set_out_shift(&c1, false, true, 20);
    sm_config_set_clkdiv(&c1, div);

    pio_gpio_init(pio, PIN_YDATA);
    pio_gpio_init(pio, PIN_SYNC);
    pio_sm_set_consecutive_pindirs(pio, sm_y, PIN_YDATA, 1, true);
    pio_sm_set_consecutive_pindirs(pio, sm_y, PIN_SYNC, 1, true); // XDATA
    pio_sm_init(pio, sm_y, offset_slave, &c1);

        // Debug pin GPIO6
    pio_gpio_init(pio, 6);
    pio_sm_set_consecutive_pindirs(pio, sm_y, 6, 1, true);
    sm_config_set_set_pins(&c1, 6, 1);

    const int shift = 32 - 20;
    uint32_t x1_bits = 0b10100000010101010101u << shift;
    uint32_t x2_bits = 0b01010101010101010101u << shift;
    uint32_t y1_bits = 0b10100000010101010101u << shift;
    uint32_t y2_bits = 0b01010101010101010101u << shift;



    // --- NEW: Reset and preload sequence for perfect sync ---
    // Disable both SMs
    pio_sm_set_enabled(pio, sm_x, false);
    pio_sm_set_enabled(pio, sm_y, false);

    // Clear FIFOs
    pio_sm_clear_fifos(pio, sm_x);
    pio_sm_clear_fifos(pio, sm_y);

    // Push initial frames (preload)
    pio_sm_put_blocking(pio, sm_x, 0b11110000111100001111u);
    pio_sm_put_blocking(pio, sm_y, 0b11110000111100001111u);
    //sleep_us(10);
    
    // Restart SMs to wrap_target
    pio_sm_restart(pio, sm_x);
    pio_sm_restart(pio, sm_y);

    // Enable both in exact sync
    pio_enable_sm_mask_in_sync(pio, (1u << sm_x) | (1u << sm_y));
    // --- END NEW ---
    
    uint32_t led_timer = to_ms_since_boot(get_absolute_time());
    uint instr = pio_encode_set(pio_y, 0);

    //[2026-07-16] Relay state and timer
    // last state written to each relay, so the GPIO is only touched on a change
    bool galvo_power_applied      = false;
    bool laser_interlock_applied  = false;
    bool laser_main_power_applied = false;
    bool laser_remote_on_applied  = false;




            // // Example: square path coordinates in mm
            // double x_coords[4] = {50, 100, 100,  50};
            // double y_coords[4] = {50,  50, 100, 100};
            // //double y_coords[4] = {100, 100, 100, 100};

            // // Arrays to hold precomputed XY2-100 frames
            // uint32_t x_frames[4];
            // uint32_t y_frames[4];

            // // ---- Precompute all frames once ----
            // for (int i = 0; i < 4; i++) {
            //     x_frames[i] = pack_x_frame(x_coords[i]);
            //     y_frames[i] = pack_y_frame(y_coords[i]);
            // }

    // ---- Parameters ----
    int shape = SHAPE_RECTANGLE;//SHAPE_CIRCLE;   // <-- Select shape here
    //int shape = SHAPE_CIRCLE;
    // Circle parameters
    double center_x = 100.0;  // mm
    double center_y = 100.0;  // mm
    double radius   = 70.0;  // mm
    // Rectangle
    double rect_x1 = 0.0, rect_y1 = 0.0;   // corner 1
    double rect_x2 = 150.0, rect_y2 = 150.0; // opposite corner


        // ---- Buffers ----
    uint32_t x_frames[MAX_POINTS];
    uint32_t y_frames[MAX_POINTS];
    int NUM_POINTS = 0;   // will be computed

    if (shape == SHAPE_CIRCLE) {
        // Perimeter of circle
        double perimeter = 2.0 * M_PI * radius;
        NUM_POINTS = (int)(perimeter / STEP_SIZE_MM + 0.5);
        if (NUM_POINTS > MAX_POINTS) NUM_POINTS = MAX_POINTS;

        for (int i = 0; i < NUM_POINTS; i++) {
            double angle = 2.0 * M_PI * i / NUM_POINTS;
            double x = center_x + radius * cos(angle);
            double y = center_y + radius * sin(angle);

            x_frames[i] = pack_x_frame(x);
            y_frames[i] = pack_y_frame(y);
        }
    }
    else if (shape == SHAPE_RECTANGLE) {
        // Perimeter of rectangle
        double width  = fabs(rect_x2 - rect_x1);
        double height = fabs(rect_y2 - rect_y1);
        double perimeter = 2.0 * (width + height);

        NUM_POINTS = (int)(perimeter / STEP_SIZE_MM + 0.5);
        if (NUM_POINTS > MAX_POINTS) NUM_POINTS = MAX_POINTS;

        double x_coords[4] = {rect_x1, rect_x2, rect_x2, rect_x1};
        double y_coords[4] = {rect_y1, rect_y1, rect_y2, rect_y2};

        int idx = 0;
        int pts_per_edge = NUM_POINTS / 4;

        for (int edge = 0; edge < 4; edge++) {
            int next = (edge + 1) % 4;
            for (int j = 0; j < pts_per_edge && idx < NUM_POINTS; j++) {
                double t = (double)j / (pts_per_edge - 1); 
                double x = x_coords[edge] + (x_coords[next] - x_coords[edge]) * t;
                double y = y_coords[edge] + (y_coords[next] - y_coords[edge]) * t;

                x_frames[idx] = pack_x_frame(x);
                y_frames[idx] = pack_y_frame(y);
                idx++;
            }
        }
        NUM_POINTS = idx; // final count
    }

        // if (shape == SHAPE_CIRCLE) {
        //     // ---- Circle ----
        //     for (int i = 0; i < NUM_POINTS; i++) {
        //         double angle = 2.0 * PI * i / NUM_POINTS;  // 0..2PI
        //         double x = center_x + radius * cos(angle);
        //         double y = center_y + radius * sin(angle);

        //         x_frames[i] = pack_x_frame(x);
        //         y_frames[i] = pack_y_frame(y);
        //     }
        // } else if (shape == SHAPE_RECTANGLE) {
        //     // ---- Rectangle ----
        //     double x_coords[4] = {rect_x1, rect_x2, rect_x2, rect_x1};
        //     double y_coords[4] = {rect_y1, rect_y1, rect_y2, rect_y2};

        //     int idx = 0;
        //     int pts_per_edge = NUM_POINTS / 4;
        //     for (int edge = 0; edge < 4; edge++) {
        //         int next = (edge + 1) % 4;
        //         for (int j = 0; j < pts_per_edge; j++) {
        //             double t = (double)j / (pts_per_edge - 1); // 0..1
        //             double x = x_coords[edge] + (x_coords[next] - x_coords[edge]) * t;
        //             double y = y_coords[edge] + (y_coords[next] - y_coords[edge]) * t;

        //             x_frames[idx] = pack_x_frame(x);
        //             y_frames[idx] = pack_y_frame(y);
        //             idx++;
        //         }
        //     }
        // }

    // Push initial word (sets X)
    // uint32_t Xword = 0b1010101010101010;
    // pio_sm_put_blocking(pio, sm_x, Xword);

    // uint32_t Yword = 0b1010101010101010;
    // pio_sm_put_blocking(pio, sm_y, Yword);
//=================loop=================
    // while (1) {

    //     //sleep_ms(1);
    //     //sleep_us(2000);
    //     //Xword = 0b01010101111101010101u;;  // flip pattern
    //     //pio_sm_put_blocking(pio, sm_x, x_frames[0]); // seamlessly updates
    //     pio_sm_put(pio, sm_x, x_frames[0]);
    //     //Yword = 0b01010101111101010101u;;  // flip pattern
    //     pio_sm_put(pio, sm_y, y_frames[0]); // seamlessly updates
        
    //     //sleep_ms(2);
    //     sleep_us(delaygalvo);
    //     //Xword = 0b10101010101010101010u;;  // flip pattern
    //     pio_sm_put(pio, sm_x, x_frames[1]); // seamlessly updates
    //     //Yword = 0b10101010101010101010u;;  // flip pattern
    //     pio_sm_put(pio, sm_y, y_frames[1]); // seamlessly updates

    //     //sleep_ms(2);
    //     sleep_us(delaygalvo);
    //     pio_sm_put(pio, sm_x, x_frames[2]); // seamlessly updates
    //     pio_sm_put(pio, sm_y, y_frames[2]); // seamlessly updates

    //     //sleep_ms(2);
    //     sleep_us(delaygalvo);
    //     pio_sm_put(pio, sm_x, x_frames[3]); // seamlessly updates
    //     pio_sm_put(pio, sm_y, y_frames[3]); // seamlessly updates
    //     //sleep_ms(2);
    //     sleep_us(delaygalvo);
    // }   
    // printf("NUM_POINTS = %d\n", NUM_POINTS);
    // sleep_ms(2000);
    
    //[2026-11-27] publish what was computed above so getStatus can report it
    api_state.num_points = NUM_POINTS;
    api_state.shape      = shape;

    while (1) {
        for (int i = 0; i < NUM_POINTS; i++) {

            // push precomputed frame
            //pio_sm_put_blocking(pio, sm_x, x_frames[i]);
            //pio_sm_put_blocking(pio, sm_y, y_frames[i]);
            //[2026-11-27] the API can pause streaming without stopping the state machines,
            // so the head holds its last position instead of the scan restarting.
            // The built in test rectangle stands aside while a layer is being scanned.
            if (api_state.galvo_streaming && !gcode_is_running()) {
                pio_sm_put(pio, sm_x, x_frames[i]); // seamlessly updates
                pio_sm_put(pio, sm_y, y_frames[i]); // seamlessly updates
            }
            //pio->txf[sm_x] = x_frames[i];
            //pio->txf[sm_y] = y_frames[i];

            sleep_us(delaygalvo);//

            api_poll();   //[2026-11-27] keep both command FIFOs drained

            //[2026-11-27] when a layer is loaded and started, the interpreter owns the PIO
            if (gcode_is_running())
                run_gcode_frames(pio, sm_x, sm_y);

            //[2026-11-27] Park the mirrors at a commanded point. One frame per axis is
            // enough: the PIO's pull is non-blocking, so with an empty FIFO it keeps
            // re-shifting the last word and the head simply holds position.
            if (api_state.galvo_position_pending) {
                api_state.galvo_position_pending = false;
                pio_sm_put(pio, sm_x, pack_x_frame(api_state.galvo_position_x_mm));
                pio_sm_put(pio, sm_y, pack_y_frame(api_state.galvo_position_y_mm));
                if (api_state.debugging_base)
                    printf("CPUC: galvo parked at X%.3f Y%.3f\n",
                           api_state.galvo_position_x_mm, api_state.galvo_position_y_mm);
            }
           
        }


        // optional: LED blink

            uint32_t now = to_ms_since_boot(get_absolute_time());
            if (now - led_timer >= 100) {
            gpio_xor_mask(1u << LED_PIN);   // toggle LED
            led_timer = now;
            //printf("rect_x1 = %.1f  ", rect_x1);
            // Somewhere in your loop:
            static int counter = 0;   // persists across calls
            counter++;
            //[2026-11-27] gated: this used to run every sweep and would bury JSON replies
            if (api_state.debugging_base)
                printf("[%d] rect_x1=%.1f  rect_x2=%.1f  rect_y1=%.1f  rect_y2=%.1f  NUM_POINTS=%d\n",
                    counter, rect_x1, rect_x2, rect_y1, rect_y2, NUM_POINTS);

            // read LED state (0 = off, >0 = on)
            // bool led_state = gpio_get(LED_PIN);

            //     if (led_state == 1) {
            //         // LED is off → small square
            //         rect_x1 = 0.0;
            //         rect_y1 = 0.0;
            //         rect_x2 = 40.0;
            //         rect_y2 = 40.0;
            //     } else {
            //         // LED is on → large square
            //         rect_x1 = 0.0;
            //         rect_y1 = 0.0;
            //         rect_x2 = 250.0;
            //         rect_y2 = 250.0;
            //     }
            }

            //[2026-11-27] Relays follow the API rather than a 5 s test toggle, so nothing is
            // energised unless something asks. Each is written only when it actually changes.
            if (galvo_power_applied != api_state.galvo_power) {
                galvo_power_applied = api_state.galvo_power;
                const bool level = galvo_power_applied ? GALVO_RLY_ACTIVE
                                                       : GALVO_RLY_INACTIVE;
                gpio_put(GALVO_NEG_RLY_PIN, level);   // both rails together:
                gpio_put(GALVO_POS_RLY_PIN, level);   // never one without the other
                if (api_state.debugging_base)
                    printf("CPUC: galvo power %s\n", galvo_power_applied ? "ON" : "OFF");
            }

            if (laser_interlock_applied != api_state.laser_interlock) {
                laser_interlock_applied = api_state.laser_interlock;
                gpio_put(LASER_INTERLOCK_RLY_PIN, laser_interlock_applied);
                if (api_state.debugging_base)
                    printf("CPUC: laser interlock %s\n", laser_interlock_applied ? "CLOSED" : "OPEN");
            }

            if (laser_main_power_applied != api_state.laser_main_power) {
                laser_main_power_applied = api_state.laser_main_power;
                gpio_put(LASER_MAIN_POWER_RLY_PIN, laser_main_power_applied);
                if (api_state.debugging_base)
                    printf("CPUC: laser main power %s\n", laser_main_power_applied ? "ON" : "OFF");
            }

            if (laser_remote_on_applied != api_state.laser_remote_on) {
                laser_remote_on_applied = api_state.laser_remote_on;
                gpio_put(LASER_REMOTE_ON_RLY_PIN, laser_remote_on_applied);
                if (api_state.debugging_base)
                    printf("CPUC: laser remote on %s\n", laser_remote_on_applied ? "ON" : "OFF");
            }

    }

}

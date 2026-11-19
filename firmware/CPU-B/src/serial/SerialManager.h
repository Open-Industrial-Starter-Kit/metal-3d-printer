#ifndef SERIAL_MANAGER_H
#define SERIAL_MANAGER_H

#include <Arduino.h>

class SerialManager {
public:
    SerialManager();
    void init();
    
    //connect
    bool connectUSB();
    //bool connectUSBNonBlocking();
    bool connectCPUA();

    //disconnect
    void disconnectUSB();
    void disconnectCPUA();

    //available bytes to read
    int availableFromUSB();
    int availableFromCPUA();

    //CPU A Serial
    size_t CPUASerialWrite(const byte _byte);
    size_t CPUASerialWrite(const char _char);

    size_t CPUASerialPrintln(const char* _char);
    size_t CPUASerialPrint(const char* _char);
    size_t CPUASerialPrintln(int _int);
    size_t CPUASerialPrint(int _int);
    size_t CPUASerialPrint(float _float);
    size_t CPUASerialPrintln(float _float);
    size_t CPUASerialPrintln();

    int readFromCPUA();

    //USB Serial
    size_t USBSerialPrintln(const char* _char);
    size_t USBSerialPrint(const char* _char);
    size_t USBSerialPrintln(int _int);
    size_t USBSerialPrint(int _int);
    size_t USBSerialPrintln(long _long);
    size_t USBSerialPrint(long _long);
    size_t USBSerialPrintln(double _double);
    size_t USBSerialPrint(double _double);
    size_t USBSerialPrint(unsigned long _long);
    size_t USBSerialPrintln(unsigned long _long);

    size_t USBSerialWrite(byte _byte);
    size_t USBSerialWrite(const char _char);

    int readFromUSB();

    // The ESP32-C3 link and the distance laser sensor are not fitted on the
    // Control Board V2 and their pins carry other functions, so both ports
    // are gone. Ethernet replaces the ESP32-C3 as the network transport.


private:

    unsigned long connectionStartTime;
    HardwareSerial& USBSerial = Serial;
    HardwareSerial& CPUASerial = Serial1;
    
};

#endif // SERIAL_MANAGER_H
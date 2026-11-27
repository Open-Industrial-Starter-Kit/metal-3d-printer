# == DO NOT EDIT THE FOLLOWING LINES for the Raspberry Pi Pico VS Code Extension to work ==
if(WIN32)
    set(USERHOME $ENV{USERPROFILE})
else()
    set(USERHOME $ENV{HOME})
endif()
set(sdkVersion 2.3.0)
set(toolchainVersion 14_2_Rel1)
set(picotoolVersion 2.3.0)
set(picoVscode ${USERHOME}/.pico-sdk/cmake/pico-vscode.cmake)
if (EXISTS ${picoVscode})
    include(${picoVscode})
endif()
# ====================================================================================
set(PICO_BOARD weact_studio_rp2350b_core CACHE STRING "Board type")

cmake_minimum_required(VERSION 3.13)
include(pico_sdk_import.cmake)

#project(pico-projects)
project(galvo_test C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()


add_executable(galvo_test
        galvo_test_with_relays.cpp
        json_api.cpp
        )

# Enable USB CDC (so printf goes to COMx port)
pico_enable_stdio_usb(galvo_test 1)
pico_enable_stdio_uart(galvo_test 0)



        target_link_libraries(galvo_test pico_stdlib hardware_pio hardware_uart)

pico_generate_pio_header(galvo_test ${CMAKE_CURRENT_LIST_DIR}/xy2_100.pio)
target_link_libraries(galvo_test pico_stdlib hardware_pio hardware_uart)

pico_add_extra_outputs(galvo_test)
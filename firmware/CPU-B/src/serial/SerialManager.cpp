#include "SerialManager.h"
#include "../Config.h"
#include "../Constants.h"
#include <Arduino.h>

SerialManager::SerialManager() {}

void SerialManager::init() {

    Serial.ignoreFlowControl(true);
    
    Serial1.setFIFOSize(SERIAL_BUFFER_SIZE);

    // Serial2 drove an external WiFi module on GP8/GP9. On this board it is the link to
    // CPU-C on GP4/GP5, so the pins are moved off the GP8/GP9 defaults BEFORE begin():
    // those are the RS-422 transceiver GM490_DI/GM490_DO and driving them would put two
    // drivers on one net.
    Serial2.setTX(CPUC_UART_TX_PIN);
    Serial2.setRX(CPUC_UART_RX_PIN);
    Serial2.setFIFOSize(SERIAL_BUFFER_SIZE);

}

/*
bool SerialManager::connectUSBNonBlocking() {

    USBSerial.begin(USB_BAUD_RATE);  // no-op if already running
    isUSBPortStartedBool = true;
    return Serial.dtr();  // true only if a PC terminal is already open

}
*/

bool SerialManager::connectUSB(){

    USBSerial.begin(USB_BAUD_RATE);  // no-op if already running
    connectionStartTime = millis();
    
    while (!Serial.dtr()) {  // cheap bitfield read, no USB overhead

        if ((millis() - connectionStartTime) > SERIAL_CONNECTION_TIMEOUT)
            return false;
            
        delay(1);
    
    }
    
    return true;
} 

bool SerialManager::connectCPUA(){

    CPUASerial.begin(CPUA_BAUD_RATE);
    connectionStartTime = millis();
    
    while (!CPUASerial) {

        if ((millis() - connectionStartTime) > SERIAL_CONNECTION_TIMEOUT) 
            return false; // Timeout occurred
        
        delay(1);
    
    }
    
    return true;
} 



bool SerialManager::connectCPUC(){

    CPUCSerial.begin(CPUC_BAUD_RATE);
    connectionStartTime = millis();

    while (!CPUCSerial) {

        if ((millis() - connectionStartTime) > SERIAL_CONNECTION_TIMEOUT)
            return false; // Timeout occurred

        delay(1);

    }

    return true;
}


void SerialManager::disconnectUSB() {USBSerial.end();}

void SerialManager::disconnectCPUA() {CPUASerial.end();}

void SerialManager::disconnectCPUC() {CPUCSerial.end();}



size_t SerialManager::CPUASerialWrite(byte _byte) {
  
    return CPUASerial.write(_byte);

}

size_t SerialManager::CPUASerialWrite(const char _char) {
    
    return CPUASerial.write(_char);

}

size_t SerialManager::CPUASerialPrint(const char* _char) {

    return CPUASerial.print(_char); 

}

size_t SerialManager::CPUASerialPrintln(const char* _char) {

    return CPUASerial.print(_char) + CPUASerial.println();

}

size_t SerialManager::CPUASerialPrint(int _int) {

    return CPUASerial.print(_int); 

}

size_t SerialManager::CPUASerialPrintln(int _int) {

    return CPUASerial.print(_int) + CPUASerial.println();

}

size_t SerialManager::CPUASerialPrintln(float _float) {

    return CPUASerial.print(_float) + CPUASerial.println();

}

size_t SerialManager::CPUASerialPrintln() {

    return CPUASerial.println();

}

size_t SerialManager::USBSerialPrint(const char* _char) {

    return USBSerial.print(_char); 

}

size_t SerialManager::USBSerialPrintln(const char* _char) {

    return USBSerial.print(_char) + USBSerial.println();

}

size_t SerialManager::USBSerialPrint(int _int) {

    return USBSerial.print(_int); 

}

size_t SerialManager::USBSerialPrintln(int _int) {

    return USBSerial.print(_int) + USBSerial.println();

}

size_t SerialManager::USBSerialPrint(double _double) {

    return USBSerial.print(_double, 3); 

}

size_t SerialManager::USBSerialPrintln(double _double) {

    return USBSerial.print(_double, 3) + USBSerial.println();
   
}

size_t SerialManager::USBSerialPrint(unsigned long _long) {

    return USBSerial.print(_long); 

}

size_t SerialManager::USBSerialPrintln(unsigned long _long) {

    return USBSerial.print(_long) + USBSerial.println();

}

size_t SerialManager::USBSerialPrint(long _long) {

    return USBSerial.print(_long); 

}

size_t SerialManager::USBSerialPrintln(long _long) {

    return USBSerial.print(_long) + USBSerial.println();

}

size_t SerialManager::USBSerialWrite(byte _byte) {
   
    return USBSerial.write(_byte);

}

size_t SerialManager::USBSerialWrite(char _char) {

    return USBSerial.write(_char);

}

int SerialManager::readFromUSB() {
    
    if (USBSerial.available() > 0) 
        return USBSerial.read();
    else 
        return -1;
    
}   

int SerialManager::readFromCPUA() {

        return CPUASerial.read();

}






int SerialManager::availableFromUSB() {

    return USBSerial.available();

}

int SerialManager::availableFromCPUA() {

    return CPUASerial.available();

}


size_t SerialManager::CPUCSerialWrite(byte _byte) {

    return CPUCSerial.write(_byte);

}

size_t SerialManager::CPUCSerialWrite(const char _char) {

    return CPUCSerial.write(_char);

}

size_t SerialManager::CPUCSerialPrint(const char* _char) {

    return CPUCSerial.print(_char);

}

size_t SerialManager::CPUCSerialPrintln(const char* _char) {

    return CPUCSerial.print(_char) + CPUCSerial.println();

}

size_t SerialManager::CPUCSerialPrint(int _int) {

    return CPUCSerial.print(_int);

}

size_t SerialManager::CPUCSerialPrintln(int _int) {

    return CPUCSerial.print(_int) + CPUCSerial.println();

}

size_t SerialManager::CPUCSerialPrint(float _float) {

    return CPUCSerial.print(_float);

}

size_t SerialManager::CPUCSerialPrintln(float _float) {

    return CPUCSerial.print(_float) + CPUCSerial.println();

}

size_t SerialManager::CPUCSerialPrintln() {

    return CPUCSerial.println();

}

int SerialManager::readFromCPUC() {

    return CPUCSerial.read();

}

int SerialManager::availableFromCPUC() {

    return CPUCSerial.available();

}

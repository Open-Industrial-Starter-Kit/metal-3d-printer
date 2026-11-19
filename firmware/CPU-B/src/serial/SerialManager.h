#ifndef SERIAL_MANAGER_H
#define SERIAL_MANAGER_H

#include <Arduino.h>
#include <SoftwareSerial.h>

class SerialManager {
public:
    SerialManager();
    void init();
    
    //connect
    bool connectUSB();
    //bool connectUSBNonBlocking();
    bool connectCPUA();
    bool connectESP32();
    bool connectDistanceLaserSensor();

    //disconnect
    void disconnectUSB();
    void disconnectCPUA();
    void disconnectESP32();
    void disconnectDistanceLaserSensor();

    //available bytes to read
    int availableFromUSB();
    int availableFromCPUA();
    int availableFromESP32();
    int availableFromDistanceLaserSensor();

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

    //ESP32 Serial
    int readFromESP32();
    size_t ESP32SerialPrintln(const char* _char);
    size_t ESP32SerialPrint(const char* _char);

    //Distance laser sensor Serial (SoftwareSerial)
    int readFromDistanceLaserSensor();
    SoftwareSerial& getDistanceLaserSensorSerial();


private:

    unsigned long connectionStartTime;
    HardwareSerial& USBSerial = Serial;
    HardwareSerial& CPUASerial = Serial1;
    HardwareSerial& ESP32Serial = Serial2;
    SoftwareSerial distanceLaserSensorSerial;
    
};

#endif // SERIAL_MANAGER_H
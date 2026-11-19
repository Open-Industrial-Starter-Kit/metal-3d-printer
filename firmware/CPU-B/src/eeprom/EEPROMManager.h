#ifndef EEPROM_MANAGER_H
#define EEPROM_MANAGER_H

#include "../Config.h"
#include "../util/ArrayUtil.h"

class EEPROMManager {

public:
    
    bool clearEEPROM();
    
    WifiSettings readWiFiSettings();
    bool saveWiFiSettings(WifiSettings wifiSettings);
    
    DebuggingSettings readDebuggingSettings();
    bool saveDebuggingSettings(DebuggingSettings debuggingSettings);

    JsonSettings readJsonSettings();
    bool saveJsonSettings(JsonSettings jsonSettings);

    MachineNameSettings readMachineNameSettings();
    bool saveMachineNameSettings(MachineNameSettings machineNameSettings);

    ToolLengthReferenceSettings readToolLengthReferenceSettings();
    bool saveToolLengthReferenceSettings(ToolLengthReferenceSettings settings);

    LaserOffsetZeroSettings readLaserOffsetZeroSettings();
    bool saveLaserOffsetZeroSettings(LaserOffsetZeroSettings settings);

    HousingSettings readHousingSettings();
    bool saveHousingSettings(HousingSettings settings);

    ProbeHeightSettings readProbeHeightSettings();
    bool saveProbeHeightSettings(ProbeHeightSettings settings);

    BrushlessMotorSettings readBrushlessMotorSettings();
    bool saveBrushlessMotorSettings(BrushlessMotorSettings settings);


private:

ArrayUtil arrayUtil;

};

#endif // EEPROM_MANAGER_H
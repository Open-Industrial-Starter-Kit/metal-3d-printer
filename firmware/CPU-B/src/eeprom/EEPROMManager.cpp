#include "EEPROMManager.h"
#include <EEPROM.h>
#include "../Config.h"
#include "../util/ArrayUtil.h"

bool EEPROMManager::clearEEPROM() {

    EEPROM.begin(EEPROM_SIZE);

    for(int i=0; i<EEPROM.length(); ++i)
        EEPROM.write(i, 0);

    return EEPROM.commit();
    EEPROM.end();

}

NetworkSettings EEPROMManager::readNetworkSettings() {

    NetworkSettings networkSettings;

    arrayUtil.clearCharArray(networkSettings.apSsid, ACCESS_POINT_SSID_CHARS);
    arrayUtil.clearCharArray(networkSettings.apPassword, ACCESS_POINT_PASSWORD_CHARS);

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(EEPROM_START_ADDRESS, networkSettings);
    EEPROM.end();

    return networkSettings;

}

bool EEPROMManager::saveNetworkSettings(NetworkSettings networkSettings) {

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.put(EEPROM_START_ADDRESS, networkSettings);
    bool success = EEPROM.commit();
    EEPROM.end();

    return success;

}

DebuggingSettings EEPROMManager::readDebuggingSettings() {

    DebuggingSettings debuggingSettings;

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(EEPROM_START_ADDRESS + sizeof(NetworkSettings), debuggingSettings);
    EEPROM.end();

    return debuggingSettings;

}

bool EEPROMManager::saveDebuggingSettings(DebuggingSettings debuggingSettings) {

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.put(EEPROM_START_ADDRESS + sizeof(NetworkSettings), debuggingSettings);
    bool success = EEPROM.commit();
    EEPROM.end();

    return success;

}

JsonSettings EEPROMManager::readJsonSettings() {

    JsonSettings jsonSettings;

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings), jsonSettings);
    EEPROM.end();

    return jsonSettings;

}

bool EEPROMManager::saveJsonSettings(JsonSettings jsonSettings) {

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.put(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings), jsonSettings);
    bool success = EEPROM.commit();
    EEPROM.end();

    return success;

}

MachineNameSettings EEPROMManager::readMachineNameSettings() {

    MachineNameSettings machineNameSettings;
    arrayUtil.clearCharArray(machineNameSettings.machineName, MACHINE_NAME_CHARS);

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings) + sizeof(JsonSettings), machineNameSettings);
    EEPROM.end();

    return machineNameSettings;

}

bool EEPROMManager::saveMachineNameSettings(MachineNameSettings machineNameSettings) {

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.put(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings) + sizeof(JsonSettings), machineNameSettings);
    bool success = EEPROM.commit();
    EEPROM.end();

    return success;

}

ToolLengthReferenceSettings EEPROMManager::readToolLengthReferenceSettings() {

    ToolLengthReferenceSettings settings;

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings) + sizeof(JsonSettings) + sizeof(MachineNameSettings), settings);
    EEPROM.end();

    return settings;

}

bool EEPROMManager::saveToolLengthReferenceSettings(ToolLengthReferenceSettings settings) {

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.put(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings) + sizeof(JsonSettings) + sizeof(MachineNameSettings), settings);
    bool success = EEPROM.commit();
    EEPROM.end();

    return success;

}

LaserOffsetZeroSettings EEPROMManager::readLaserOffsetZeroSettings() {

    LaserOffsetZeroSettings settings;

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings) + sizeof(JsonSettings) + sizeof(MachineNameSettings) + sizeof(ToolLengthReferenceSettings), settings);
    EEPROM.end();

    return settings;

}

bool EEPROMManager::saveLaserOffsetZeroSettings(LaserOffsetZeroSettings settings) {

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.put(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings) + sizeof(JsonSettings) + sizeof(MachineNameSettings) + sizeof(ToolLengthReferenceSettings), settings);
    bool success = EEPROM.commit();
    EEPROM.end();

    return success;

}

HousingSettings EEPROMManager::readHousingSettings() {

    HousingSettings settings;

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings) + sizeof(JsonSettings) + sizeof(MachineNameSettings) + sizeof(ToolLengthReferenceSettings) + sizeof(LaserOffsetZeroSettings), settings);
    EEPROM.end();

    return settings;

}

bool EEPROMManager::saveHousingSettings(HousingSettings settings) {

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.put(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings) + sizeof(JsonSettings) + sizeof(MachineNameSettings) + sizeof(ToolLengthReferenceSettings) + sizeof(LaserOffsetZeroSettings), settings);
    bool success = EEPROM.commit();
    EEPROM.end();

    return success;

}

ProbeHeightSettings EEPROMManager::readProbeHeightSettings() {

    ProbeHeightSettings settings;

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings) + sizeof(JsonSettings) + sizeof(MachineNameSettings) + sizeof(ToolLengthReferenceSettings) + sizeof(LaserOffsetZeroSettings) + sizeof(HousingSettings), settings);
    EEPROM.end();

    return settings;

}

bool EEPROMManager::saveProbeHeightSettings(ProbeHeightSettings settings) {

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.put(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings) + sizeof(JsonSettings) + sizeof(MachineNameSettings) + sizeof(ToolLengthReferenceSettings) + sizeof(LaserOffsetZeroSettings) + sizeof(HousingSettings), settings);
    bool success = EEPROM.commit();
    EEPROM.end();

    return success;

}

BrushlessMotorSettings EEPROMManager::readBrushlessMotorSettings() {

    BrushlessMotorSettings settings;

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings) + sizeof(JsonSettings) + sizeof(MachineNameSettings) + sizeof(ToolLengthReferenceSettings) + sizeof(LaserOffsetZeroSettings) + sizeof(HousingSettings) + sizeof(ProbeHeightSettings), settings);
    EEPROM.end();

    return settings;

}

bool EEPROMManager::saveBrushlessMotorSettings(BrushlessMotorSettings settings) {

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.put(EEPROM_START_ADDRESS + sizeof(NetworkSettings) + sizeof(DebuggingSettings) + sizeof(JsonSettings) + sizeof(MachineNameSettings) + sizeof(ToolLengthReferenceSettings) + sizeof(LaserOffsetZeroSettings) + sizeof(HousingSettings) + sizeof(ProbeHeightSettings), settings);
    bool success = EEPROM.commit();
    EEPROM.end();

    return success;

}

#include "EEPROMManager.h"
#include <EEPROM.h>
#include "../Config.h"
#include "../util/ArrayUtil.h"

// EEPROM record layout. Each record is stored back-to-back from EEPROM_START_ADDRESS and is
// validated on read through its checkChar, so a record that was never written (or whose offset
// moved because the layout changed) reads as uninitialised and the caller falls back to its
// defaults. To add a record: append it at the end of the chain, i.e. offset =
// EEPROM_START_ADDRESS + sizeof(every record already declared above it).
//
//   0                                  DebuggingSettings
//   sizeof(DebuggingSettings)          JsonSettings

bool EEPROMManager::clearEEPROM() {

    EEPROM.begin(EEPROM_SIZE);

    for(int i=0; i<EEPROM.length(); ++i)
        EEPROM.write(i, 0);

    return EEPROM.commit();
    EEPROM.end();

}



DebuggingSettings EEPROMManager::readDebuggingSettings() {

    DebuggingSettings debuggingSettings;

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(EEPROM_START_ADDRESS, debuggingSettings);
    EEPROM.end();

    return debuggingSettings;

}

bool EEPROMManager::saveDebuggingSettings(DebuggingSettings debuggingSettings) {

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.put(EEPROM_START_ADDRESS, debuggingSettings);
    bool success = EEPROM.commit();
    EEPROM.end();

    return success;

}

JsonSettings EEPROMManager::readJsonSettings() {

    JsonSettings jsonSettings;

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(EEPROM_START_ADDRESS + sizeof(DebuggingSettings), jsonSettings);
    EEPROM.end();

    return jsonSettings;

}

bool EEPROMManager::saveJsonSettings(JsonSettings jsonSettings) {

    EEPROM.begin(EEPROM_SIZE);
    EEPROM.put(EEPROM_START_ADDRESS + sizeof(DebuggingSettings), jsonSettings);
    bool success = EEPROM.commit();
    EEPROM.end();

    return success;

}













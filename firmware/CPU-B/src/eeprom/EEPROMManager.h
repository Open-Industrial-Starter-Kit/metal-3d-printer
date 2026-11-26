#ifndef EEPROM_MANAGER_H
#define EEPROM_MANAGER_H

#include "../Config.h"
#include "../util/ArrayUtil.h"

class EEPROMManager {

public:
    
    bool clearEEPROM();
    
    
    DebuggingSettings readDebuggingSettings();
    bool saveDebuggingSettings(DebuggingSettings debuggingSettings);

    JsonSettings readJsonSettings();
    bool saveJsonSettings(JsonSettings jsonSettings);








private:

ArrayUtil arrayUtil;

};

#endif // EEPROM_MANAGER_H
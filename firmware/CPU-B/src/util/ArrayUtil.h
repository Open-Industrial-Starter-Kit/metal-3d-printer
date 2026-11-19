#ifndef ARRAY_UTIL_H
#define ARRAY_UTIL_H

#include <Arduino.h>
#include "../Constants.h"
#include <cstddef>
#include <cstdint>
#include <cstring>

class ArrayUtil {
public:
    void clearCharArray(char _array[], int pos);
    void clearByteArray(uint8_t _array[], int pos);
    void clearSyncActions(SyncAction syncActions[]);
    void clearSyncAction(SyncAction syncAction);

    template <size_t Lines, size_t Chars>
    void clear2DCharArray(char (&array)[Lines][Chars]) {
        for (size_t line = 0; line < Lines; ++line)
            clearCharArray(array[line], static_cast<int>(Chars));
    }
    
    int firstEmptyAction(SyncAction syncActions[]);
    int lastSyncAction(SyncAction syncActions[]);
    int firstSyncAction(SyncAction syncActions[]);

    template <size_t Lines, size_t Chars>
    int firstMacroBufferLine(char (&array)[Lines][Chars]) {
        for (size_t line = 0; line < Lines; ++line)
            if (array[line][0] != NULL_CHAR)
                return static_cast<int>(line);

        return -1;
    }

    void removeAllSubstrings(char *str, const char *substr);
    bool encodeBytesToBase62(const uint8_t* input, size_t inputLen, char* output, size_t outputSize);

    template <size_t DestinationLines, size_t DestinationChars, size_t SourceLines, size_t SourceChars>
    void copyMacro(char (&destination)[DestinationLines][DestinationChars], const char (&source)[SourceLines][SourceChars]) {
        const size_t linesToCopy = DestinationLines < SourceLines ? DestinationLines : SourceLines;

        for (size_t line = 0; line < linesToCopy; ++line) {
            if (source[line][0] == NULL_CHAR)
                break;

            strncpy(destination[line], source[line], DestinationChars - 1);
            destination[line][DestinationChars - 1] = '\0';
        }
    }


private:
};

#endif // ARRAY_UTIL_H
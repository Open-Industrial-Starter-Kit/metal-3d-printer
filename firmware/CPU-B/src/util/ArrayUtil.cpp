#include "ArrayUtil.h"
#include "../Constants.h"
#include "../Config.h"
#include <cstring>
#include <cstddef>

void ArrayUtil::clearCharArray(char _array[], int pos) {
    memset(_array, '\0', pos);
}

void ArrayUtil::clearSyncActions(SyncAction syncActions[]) {

  for (int C = 0; C < SYNC_ACTION_AMOUNT; C++) {

    clearCharArray(syncActions[C].type, SYNC_ACTION_CHARS);
    syncActions[C].positionX = 0;
    syncActions[C].positionY = 0;
    syncActions[C].positionZ = 0;
    syncActions[C].positionRange = 0;
    syncActions[C].pollingInterval = 0;
  }

}

void ArrayUtil::clearSyncAction(SyncAction syncAction) {

  clearCharArray(syncAction.type, SYNC_ACTION_CHARS);
  syncAction.positionX = 0;
  syncAction.positionY = 0;
  syncAction.positionZ = 0;
  syncAction.positionRange = 0;
  syncAction.pollingInterval = 0;

}

int ArrayUtil::firstEmptyAction(SyncAction syncActions[]) {

  for (int C = 0; C < SYNC_ACTION_AMOUNT; C++)
    if (syncActions[C].type[0] == NULL_CHAR)
      return C;

  return -1;
}

int ArrayUtil::lastSyncAction(SyncAction syncActions[]) {

  for (int C = SYNC_ACTION_AMOUNT - 1; C > -1; C--)
    if (syncActions[C].type[0] != NULL_CHAR)
      return C;

  return -1;
}

int ArrayUtil::firstSyncAction(SyncAction syncActions[]) {

  for (int C = 0; C < SYNC_ACTION_AMOUNT; C++)
    if (syncActions[C].type[0] != NULL_CHAR)
      return C;

  return -1;

}

void ArrayUtil::removeAllSubstrings(char *str, const char *substr) {
    
    size_t len_remove = strlen(substr);
    while (len_remove > 0) {
        char *pos = strstr(str, substr);
        if (!pos) break;
        memmove(pos, pos + len_remove, strlen(pos + len_remove) + 1);
    }

}

void ArrayUtil::clearByteArray(uint8_t _array[], int pos) {
    memset(_array, 0, pos);
} 

bool ArrayUtil::encodeBytesToBase62(const uint8_t* input, size_t inputLen, char* output, size_t outputSize) {

  const char alphabet[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
  const size_t maxInputBytes = 64;
  const size_t maxEncodedChars = 128;

  if (input == nullptr || output == nullptr || outputSize == 0) {
    return false;
  }

  if (inputLen == 0) {
    if (outputSize < 2) {
      return false;
    }
    output[0] = '0';
    output[1] = '\0';
    return true;
  }

  if (inputLen > maxInputBytes) {
    return false;
  }

  uint8_t working[maxInputBytes];
  memcpy(working, input, inputLen);

  char reversed[maxEncodedChars];
  size_t reversedLen = 0;
  size_t start = 0;

  while (start < inputLen) {
    uint32_t remainder = 0;

    for (size_t i = start; i < inputLen; ++i) {
      uint32_t accumulator = (remainder << 8) | working[i];
      working[i] = static_cast<uint8_t>(accumulator / 62U);
      remainder = accumulator % 62U;
    }

    if (reversedLen >= (maxEncodedChars - 1)) {
      return false;
    }

    reversed[reversedLen++] = alphabet[remainder];

    while (start < inputLen && working[start] == 0) {
      ++start;
    }
  }

  if ((reversedLen + 1) > outputSize) {
    return false;
  }

  for (size_t i = 0; i < reversedLen; ++i) {
    output[i] = reversed[reversedLen - 1 - i];
  }

  output[reversedLen] = '\0';
  return true;
}


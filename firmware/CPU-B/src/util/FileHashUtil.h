#ifndef FILE_HASH_UTIL_H
#define FILE_HASH_UTIL_H

#include <Arduino.h>
#include <pico/sha256.h>

class FileHashUtil {
public:
  FileHashUtil();

  bool start();
  bool update(const uint8_t* data, size_t dataSize);
  bool update(const char* text);
  bool updateLine(const char* text);
  bool finishHex(char* outputBuffer, size_t outputBufferSize);
  void abort();

private:
  pico_sha256_state_t state;
  bool active;
};

#endif
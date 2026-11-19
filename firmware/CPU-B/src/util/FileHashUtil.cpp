#include "FileHashUtil.h"

FileHashUtil::FileHashUtil() : active(false) {
}

bool FileHashUtil::start() {
  abort();
  active = pico_sha256_start_blocking(&state, SHA256_BIG_ENDIAN, true) == PICO_OK;
  return active;
}

bool FileHashUtil::update(const uint8_t* data, size_t dataSize) {
  if (!active)
    return false;

  pico_sha256_update_blocking(&state, data, dataSize);
  return true;
}

bool FileHashUtil::update(const char* text) {
  if (text == nullptr)
    return false;

  return update(reinterpret_cast<const uint8_t*>(text), strlen(text));
}

bool FileHashUtil::updateLine(const char* text) {
  static const uint8_t newline = '\n';

  if (!update(text))
    return false;

  return update(&newline, 1);
}

bool FileHashUtil::finishHex(char* outputBuffer, size_t outputBufferSize) {
  if (!active || outputBuffer == nullptr || outputBufferSize < (SHA256_RESULT_BYTES * 2 + 1))
    return false;

  sha256_result_t result;
  pico_sha256_finish(&state, &result);
  active = false;

  static const char hexDigits[] = "0123456789abcdef";
  for (size_t i = 0; i < SHA256_RESULT_BYTES; ++i) {
    outputBuffer[(i * 2)] = hexDigits[(result.bytes[i] >> 4) & 0x0F];
    outputBuffer[(i * 2) + 1] = hexDigits[result.bytes[i] & 0x0F];
  }
  outputBuffer[SHA256_RESULT_BYTES * 2] = '\0';
  return true;
}

void FileHashUtil::abort() {
  if (!active)
    return;

  pico_sha256_finish(&state, nullptr);
  active = false;
}
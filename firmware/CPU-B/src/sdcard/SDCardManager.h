#ifndef SDCARD_MANAGER_H
#define SDCARD_MANAGER_H

#include <Arduino.h>
#include <SdFat.h>
#include "../Config.h"

class SDCardManager {
public:
  // SD Card initialization and management
  void init();
  bool connect();
  void disconnect();
  uint64_t getCardSize();
  uint64_t getFreeSpace();

  // File listing
  int listFiles(const char* extension, char fileList[][SD_MAX_FILE_NAME_LENGTH], int maxFiles);
  int listFilesWithSize(const char* extension, char fileList[][SD_MAX_FILE_NAME_LENGTH], long fileSizes[], int maxFiles);
  int listFilesWithSizeAndDate(const char* extension, char fileList[][SD_MAX_FILE_NAME_LENGTH], long fileSizes[], char fileDates[][20], int maxFiles);

  // G-code file operations
  bool openGCodeFiletoRead(const char* filename);
  bool readGCodeLine(char* buffer, size_t bufferSize);
  bool readFirstLine(const char* filename, char* buffer, size_t bufferSize);
  bool openProcessingFileToRead(const char* filename);
  bool readProcessingLine(char* buffer, size_t bufferSize);
  bool openDownloadFileToRead(const char* filename);
  bool readDownloadChunk(char* buffer, size_t bufferSize, unsigned long* linesRead, bool* eof);
  long getDownloadFilePosition();          // current byte offset in the download file, -1 if not open
  bool seekDownloadFile(uint32_t pos);     // rewind/seek the download file (used to re-send a failed chunk)
  long countFileLines(const char* filename);
  long countNonCommentFileLines(const char* filename);

  // Generic file operations
  void closeFile();
  void closeProcessingFile();
  void closeDownloadFile();
  bool isReadFileOpen();
  bool isProcessingFileOpen();
  bool isDownloadFileOpen();
  bool isWriteFileOpen();
  bool isFileAvailable();
  long getOpenedFileSize();
  long getFilePosition();

  bool fileExists(const char* filename);
  // Fixed-offset binary access (used by the hidden usage meter; create-if-missing).
  bool writeBinaryAt(const char* filename, uint32_t offset, const uint8_t* data, size_t len);
  int  readBinaryAt(const char* filename, uint32_t offset, uint8_t* data, size_t len); // bytes read, -1 on error
  bool isNcFilename(const char* filename);
  bool deleteFile(const char* filename);
  bool renameFile(const char* oldName, const char* newName);
  long getFileSize(const char* filename);

   //checked till here!

  // Generic text file operations - Writing
  bool createTextFile(const char* filename);
  bool openTextFileForWriting(const char* filename);
  bool openTextFileForAppending(const char* filename);
  bool writeTextLine(const char* text);
  bool writeText(const char* text);
  bool syncWriteFile();
  uint32_t getWriteFilePosition();
  bool seekWriteFile(uint32_t pos);
  void closeWriteFile();
  bool setWriteFileTimestamp(uint16_t year, uint8_t month, uint8_t day, uint8_t hour, uint8_t minute, uint8_t second);

  // Directory operations
  bool createDirectory(const char* path);
  bool removeDirectory(const char* path);
  bool changeDirectory(const char* path);
  void getCurrentDirectory(char* buffer, size_t bufferSize);
 
  // Info retrieval
  bool getCardInfo(char* buffer, size_t bufferSize);

  bool connected;

private:
  SdFat sdCard;
  FsFile readFile;       // For reading files
  FsFile processingReadFile; // For metadata prepend processing
  FsFile downloadReadFile; // For download sessions
  FsFile writeFile;      // For writing files
  char readBuffer[SD_BUFFER_SIZE];
  char downloadLineBuffer[LINE_BUFFER_CHARACTERS];
  char writeBuffer[SD_BUFFER_SIZE];
  size_t writeBufferLength;
  
  // Helper methods
  bool hasExtension(const char* filename, const char* extension);
  bool isHashSidecarFilename(const char* filename);
  void toUpperCase(char* str);
  bool appendToWriteBuffer(const char* data, size_t dataLength);
  bool flushWriteBuffer();
};

#endif // SDCARD_MANAGER_H
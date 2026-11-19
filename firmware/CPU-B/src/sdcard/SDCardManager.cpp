#include "SDCardManager.h"
#include "../Config.h"
#include "../Constants.h"
#include "../util/GCodeBoundsUtil.h"

void SDCardManager::init() {

  connected = false;
  writeBufferLength = 0;

}

// ==================== SD Card Initialization ====================

bool SDCardManager::connect() {

  if (connected) 
    return true;
  
  // Initialize SD card with specified CS pin and SPI speed
  if (!sdCard.begin(SD_CS_PIN, SD_SCK_MHZ(18))) 
    return false;
  
  connected = true;
  return true;
}

void SDCardManager::disconnect() {

  if (readFile.isOpen())
    readFile.close();

  if (processingReadFile.isOpen())
    processingReadFile.close();

  if (downloadReadFile.isOpen())
    downloadReadFile.close();
  
  if (writeFile.isOpen()) {
    flushWriteBuffer();
    writeFile.sync();
    writeFile.close();
  }

  writeBufferLength = 0;
  connected = false;
}

//returns card size in MB
uint64_t SDCardManager::getCardSize() {

  if (!connected) {
    return -1;
  }
  
  return (sdCard.card()->sectorCount() * 512ULL)/1000000;

}

uint64_t SDCardManager::getFreeSpace() {
 
    if (!connected) {
    return -1;
  }

  return (sdCard.freeClusterCount() * sdCard.bytesPerCluster())/1000000;
  
}

// ==================== File Listing ====================

int SDCardManager::listFiles(const char* extension, char fileList[][SD_MAX_FILE_NAME_LENGTH], int maxFiles) {
  
  if (!connected) 
    return -1;
  
  FsFile root;

  if (!root.open("/"))
    return -1;

  int fileCount = 0;
  FsFile file;
  
  while (file.openNext(&root, O_RDONLY) && fileCount < maxFiles) {
    if (!file.isDir()) {
      char filename[SD_MAX_FILE_NAME_LENGTH];
      file.getName(filename, sizeof(filename));
      
      if(filename[0] == '.') {
        file.close();
        continue; // Skip hidden files
      }

      if (isHashSidecarFilename(filename)) {
        file.close();
        continue;
      }
      
      if (extension == nullptr || hasExtension(filename, extension)) {
        strncpy(fileList[fileCount], filename, SD_MAX_FILE_NAME_LENGTH - 1);
        fileList[fileCount][SD_MAX_FILE_NAME_LENGTH - 1] = '\0';
        fileCount++;
      }
    }
    file.close();
  }
  
  root.close();
  return fileCount;

}

int SDCardManager::listFilesWithSize(const char* extension, char fileList[][SD_MAX_FILE_NAME_LENGTH], long fileSizes[], int maxFiles) {

  if (!connected)
    return -1;

  FsFile root;

  if (!root.open("/"))
    return -1;

  int fileCount = 0;
  FsFile file;

  while (file.openNext(&root, O_RDONLY) && fileCount < maxFiles) {
    if (!file.isDir()) {
      char filename[SD_MAX_FILE_NAME_LENGTH];
      file.getName(filename, sizeof(filename));

      if (filename[0] == '.') {
        file.close();
        continue;
      }

      if (isHashSidecarFilename(filename)) {
        file.close();
        continue;
      }

      if (extension == nullptr || hasExtension(filename, extension)) {
        strncpy(fileList[fileCount], filename, SD_MAX_FILE_NAME_LENGTH - 1);
        fileList[fileCount][SD_MAX_FILE_NAME_LENGTH - 1] = '\0';
        fileSizes[fileCount] = (long)file.fileSize();
        fileCount++;
      }
    }
    file.close();
  }

  root.close();
  return fileCount;

}

int SDCardManager::listFilesWithSizeAndDate(const char* extension, char fileList[][SD_MAX_FILE_NAME_LENGTH], long fileSizes[], char fileDates[][20], int maxFiles) {

  if (!connected)
    return -1;

  FsFile root;

  if (!root.open("/"))
    return -1;

  int fileCount = 0;
  FsFile file;

  while (file.openNext(&root, O_RDONLY) && fileCount < maxFiles) {
    if (!file.isDir()) {
      char filename[SD_MAX_FILE_NAME_LENGTH];
      file.getName(filename, sizeof(filename));

      if (filename[0] == '.') {
        file.close();
        continue;
      }

      if (isHashSidecarFilename(filename)) {
        file.close();
        continue;
      }

      if (extension == nullptr || hasExtension(filename, extension)) {
        strncpy(fileList[fileCount], filename, SD_MAX_FILE_NAME_LENGTH - 1);
        fileList[fileCount][SD_MAX_FILE_NAME_LENGTH - 1] = '\0';
        fileSizes[fileCount] = (long)file.fileSize();

        uint16_t fatDate = 0;
        uint16_t fatTime = 0;
        if (file.getModifyDateTime(&fatDate, &fatTime)) {
          snprintf(
            fileDates[fileCount],
            20,
            "%04u-%02u-%02u %02u:%02u:%02u",
            FS_YEAR(fatDate),
            FS_MONTH(fatDate),
            FS_DAY(fatDate),
            FS_HOUR(fatTime),
            FS_MINUTE(fatTime),
            FS_SECOND(fatTime)
          );
        } else {
          fileDates[fileCount][0] = '\0';
        }

        fileCount++;
      }
    }
    file.close();
  }

  root.close();
  return fileCount;

}

// ==================== G-code File Operations ====================

bool SDCardManager::openGCodeFiletoRead(const char* filename) {

  if (!connected) 
    return false;

    //check here if it is a gcode file by extension, if not return false
    for(int i = 0; i< SUPPORTED_GCODE_EXTENSIONS_AMOUNT; i++){
        if(strstr(filename, SUPPORTED_GCODE_EXTENSIONS[i]) != NULL){
            
            if (readFile.isOpen())
                readFile.close();
  
            if (!readFile.open(filename, O_RDONLY)) 
                return false;

            char firstLineBuffer[LINE_BUFFER_CHARACTERS] = {0};
            int bytesRead = readFile.fgets(firstLineBuffer, sizeof(firstLineBuffer));

            if (bytesRead > 0) {
              int len = strlen(firstLineBuffer);
              while (len > 0 && (firstLineBuffer[len - 1] == NEWLINE || firstLineBuffer[len - 1] == RETURN)) {
                firstLineBuffer[len - 1] = NULL_CHAR;
                --len;
              }

              if (!GCodeBoundsUtil::isMetadataLine(firstLineBuffer) && !readFile.seekSet(0)) {
                readFile.close();
                return false;
              }
            }
  
            return true;

        }

    }

    return false;  // Not a supported G-code file extension

}

bool SDCardManager::readGCodeLine(char* buffer, size_t bufferSize) {
  
    if (!readFile.isOpen()) 
        return false;
  
  int bytesRead = readFile.fgets(buffer, bufferSize);
  
  if (bytesRead <= 0)
    return false;

  // Remove trailing newline/carriage return
  int len = strlen(buffer);
  while (len > 0 && (buffer[len-1] == '\n' || buffer[len-1] == '\r')) {
    buffer[len-1] = '\0';
    len--;
  }

  return true;
}

bool SDCardManager::readFirstLine(const char* filename, char* buffer, size_t bufferSize) {

  if (!connected || filename == nullptr || buffer == nullptr || bufferSize == 0)
    return false;

  FsFile file;
  if (!file.open(filename, O_RDONLY))
    return false;

  int bytesRead = file.fgets(buffer, bufferSize);
  file.close();

  if (bytesRead <= 0)
    return false;

  int len = strlen(buffer);
  while (len > 0 && (buffer[len - 1] == NEWLINE || buffer[len - 1] == RETURN)) {
    buffer[len - 1] = NULL_CHAR;
    --len;
  }

  return true;

}

bool SDCardManager::openProcessingFileToRead(const char* filename) {

  if (!connected)
    return false;

  if (processingReadFile.isOpen())
    processingReadFile.close();

  if (!processingReadFile.open(filename, O_RDONLY))
    return false;

  return true;

}

bool SDCardManager::readProcessingLine(char* buffer, size_t bufferSize) {

  if (!processingReadFile.isOpen())
    return false;

  int bytesRead = processingReadFile.fgets(buffer, bufferSize);

  if (bytesRead <= 0)
    return false;

  int len = strlen(buffer);
  while (len > 0 && (buffer[len - 1] == '\n' || buffer[len - 1] == '\r')) {
    buffer[len - 1] = '\0';
    --len;
  }

  return true;

}

bool SDCardManager::openDownloadFileToRead(const char* filename) {

  if (!connected)
    return false;

  for(int i = 0; i < SUPPORTED_GCODE_EXTENSIONS_AMOUNT; i++) {
    if(strstr(filename, SUPPORTED_GCODE_EXTENSIONS[i]) != NULL) {

      if (downloadReadFile.isOpen())
        downloadReadFile.close();

      if (!downloadReadFile.open(filename, O_RDONLY))
        return false;

      return true;
    }
  }

  return false;

}

bool SDCardManager::readDownloadChunk(char* buffer, size_t bufferSize, unsigned long* linesRead, bool* eof) {

  if (!downloadReadFile.isOpen() || buffer == nullptr || bufferSize == 0 || linesRead == nullptr || eof == nullptr)
    return false;

  buffer[0] = NULL_CHAR;
  *linesRead = 0;
  *eof = false;

  while (downloadReadFile.available()) {
    uint32_t lineStartPos = downloadReadFile.position();
    int bytesRead = downloadReadFile.fgets(downloadLineBuffer, sizeof(downloadLineBuffer));

    if (bytesRead <= 0)
      break;

    int lineLen = strlen(downloadLineBuffer);
    while (lineLen > 0 && (downloadLineBuffer[lineLen - 1] == NEWLINE || downloadLineBuffer[lineLen - 1] == RETURN)) {
      downloadLineBuffer[lineLen - 1] = NULL_CHAR;
      --lineLen;
    }

    if (lineLen == 0)
      continue;
    if (downloadLineBuffer[0] == ';' && !GCodeBoundsUtil::isMetadataLine(downloadLineBuffer))
      continue;

    size_t currentLen = strlen(buffer);
    size_t requiredLen = lineLen + strlen(LINE_CHUNK_SEPARATOR);
    if (currentLen + requiredLen >= bufferSize) {
      if (currentLen > 0) {
        // Chunk has data already — save this line for the next chunk.
        downloadReadFile.seekSet(lineStartPos);
        break;
      }
      // Chunk is empty and the line is too long to ever fit: skip it.
      // If fgets gave a partial read (no newline consumed, so bytesRead == lineLen),
      // drain the rest of the line so the file pointer advances past it.
      if (bytesRead == lineLen) {
        char drain;
        while (downloadReadFile.available()) {
          downloadReadFile.read(&drain, 1);
          if (drain == NEWLINE) break;
        }
      }
      continue;
    }

    strcat(buffer, downloadLineBuffer);
    strcat(buffer, LINE_CHUNK_SEPARATOR);
    ++(*linesRead);
  }

  *eof = !downloadReadFile.available();

  if(*linesRead == 0 && !*eof)
    return false;

  return true;

}

long SDCardManager::getDownloadFilePosition() {

  if (!downloadReadFile.isOpen())
    return -1;

  return (long)downloadReadFile.position();

}

bool SDCardManager::seekDownloadFile(uint32_t pos) {

  if (!downloadReadFile.isOpen())
    return false;

  return downloadReadFile.seekSet(pos);

}

long SDCardManager::countFileLines(const char* filename) {

  if (!connected)
    return -1;

  FsFile file;
  if (!file.open(filename, O_RDONLY))
    return -1;

  char buffer[SD_BUFFER_SIZE];
  long lineCount = 0;
  int bytesRead = 0;
  char lastChar = NULL_CHAR;

  while ((bytesRead = file.read(buffer, sizeof(buffer))) > 0) {
    for (int i = 0; i < bytesRead; ++i) {
      if (buffer[i] == NEWLINE)
        ++lineCount;
      lastChar = buffer[i];
    }
  }

  file.close();

  if (lastChar != NULL_CHAR && lastChar != NEWLINE && lastChar != RETURN)
    ++lineCount;

  return lineCount;
}

long SDCardManager::countNonCommentFileLines(const char* filename) {

  if (!connected)
    return -1;

  FsFile file;
  if (!file.open(filename, O_RDONLY))
    return -1;

  char lineBuffer[LINE_BUFFER_CHARACTERS];
  long lineCount = 0;

  while (file.available()) {
    int bytesRead = file.fgets(lineBuffer, sizeof(lineBuffer));
    if (bytesRead <= 0)
      break;

    int len = strlen(lineBuffer);
    while (len > 0 && (lineBuffer[len - 1] == NEWLINE || lineBuffer[len - 1] == RETURN)) {
      lineBuffer[len - 1] = NULL_CHAR;
      --len;
    }

    if (len == 0)
      continue;
    if (lineBuffer[0] == ';' && !GCodeBoundsUtil::isMetadataLine(lineBuffer))
      continue;

    ++lineCount;
  }

  file.close();
  return lineCount;

}

// ==================== Generic File Operations and Utilities ====================

void SDCardManager::closeFile() {

  if (readFile.isOpen()) 
    readFile.close();
  
}

void SDCardManager::closeProcessingFile() {

  if (processingReadFile.isOpen())
    processingReadFile.close();

}

void SDCardManager::closeDownloadFile() {

  if (downloadReadFile.isOpen())
    downloadReadFile.close();

}

bool SDCardManager::isReadFileOpen() {
  
    return readFile.isOpen();

}

bool SDCardManager::isProcessingFileOpen() {
  return processingReadFile.isOpen();
}

  bool SDCardManager::isDownloadFileOpen() {

    return downloadReadFile.isOpen();

  }

bool SDCardManager::isWriteFileOpen() {
  
    return writeFile.isOpen();

}


bool SDCardManager::isFileAvailable() {
  
    return readFile.isOpen() && readFile.available();

}

long SDCardManager::getOpenedFileSize() {
    
  if (!readFile.isOpen()) 
    return -1;
  
  return readFile.size()/1000000;  // Return size in MB
}

long SDCardManager::getFilePosition() {
  
    if (!readFile.isOpen())
    return -1;
  
  return readFile.position();

}


//checked till here!

bool SDCardManager::fileExists(const char* filename) {

    if (!connected)
    return false;

  return sdCard.exists(filename);
}

bool SDCardManager::writeBinaryAt(const char* filename, uint32_t offset, const uint8_t* data, size_t len) {

  if (!connected || data == nullptr)
    return false;

  FsFile f;
  if (!f.open(filename, O_RDWR | O_CREAT))
    return false;

  bool ok = f.seekSet(offset) && (f.write(data, len) == len);
  f.sync();
  f.close();

  return ok;

}

int SDCardManager::readBinaryAt(const char* filename, uint32_t offset, uint8_t* data, size_t len) {

  if (!connected || data == nullptr)
    return -1;

  FsFile f;
  if (!f.open(filename, O_RDONLY))
    return -1;

  if (!f.seekSet(offset)) {
    f.close();
    return -1;
  }

  int bytesRead = f.read(data, len);
  f.close();

  return bytesRead;

}

bool SDCardManager::isNcFilename(const char* filename) {
  return hasExtension(filename, NC_FILE_EXTENSION);
}

bool SDCardManager::deleteFile(const char* filename) {
  
  if (!connected) 
    return false;
  

  if (!sdCard.exists(filename)) 
    return false;

  if (!sdCard.remove(filename)) 
    return false;
  

  return true;
}

bool SDCardManager::renameFile(const char* oldName, const char* newName) {
  if (!connected) 
    return false;
  

  if (!sdCard.exists(oldName)) 
    return false;
  

  if (!sdCard.rename(oldName, newName)) 
    return false;

  return true;
}

long SDCardManager::getFileSize(const char* filename) {
  if (!connected) 
    return -1;
  

  FsFile file;
  if (!file.open(filename, O_RDONLY)) 
    return -1;
  
  long size = file.size();
  file.close();
  return size;
}

// ==================== Generic Text File Operations - Writing ====================

bool SDCardManager::createTextFile(const char* filename) {

  if (!connected) 
    return false;

  // Close any open write file
  if (writeFile.isOpen()) {
    flushWriteBuffer();
    writeFile.sync();
    writeFile.close();
  }

  writeBufferLength = 0;

  // Check if file exists
  if (sdCard.exists(filename)) 
    return false;

  // Create and open new file
  if (!writeFile.open(filename, O_WRONLY | O_CREAT))
    return false;

    
  writeFile.close();
  

  return true;
}

bool SDCardManager::openTextFileForWriting(const char* filename) {
  
  if (!connected) 
    return false;
  

  if (writeFile.isOpen()) {
    flushWriteBuffer();
    writeFile.sync();
    writeFile.close();
  }

  // Open file for writing (truncate if exists)
  if (!writeFile.open(filename, O_WRONLY | O_CREAT | O_TRUNC)) {
  
    return false;
  }

  writeBufferLength = 0;

  return true;
}

bool SDCardManager::openTextFileForAppending(const char* filename) {
  if (!connected) {
   
    return false;
  }

  if (writeFile.isOpen()) {
    flushWriteBuffer();
    writeFile.sync();
    writeFile.close();
  }

  // Open file for appending
  if (!writeFile.open(filename, O_WRONLY | O_CREAT | O_APPEND)) {
   
    return false;
  }

  writeBufferLength = 0;

  return true;
}

bool SDCardManager::writeTextLine(const char* text) {
  
  if (!writeFile.isOpen())
    return false;

  size_t textLength = strlen(text);

  if (!appendToWriteBuffer(text, textLength))
    return false;

  const char newline = '\n';
  if (!appendToWriteBuffer(&newline, 1))
    return false;

  return true;

}

bool SDCardManager::writeText(const char* text) {
  if (!writeFile.isOpen()) {
    
    return false;
  }

  size_t textLength = strlen(text);

  if (!appendToWriteBuffer(text, textLength)) {
   
    return false;
  }

  return true;
}

bool SDCardManager::syncWriteFile() {
  if (!writeFile.isOpen())
    return false;
  if (!flushWriteBuffer())
    return false;
  writeFile.sync();
  return true;
}

uint32_t SDCardManager::getWriteFilePosition() {
  if (!writeFile.isOpen())
    return 0;
  flushWriteBuffer();
  return (uint32_t)writeFile.curPosition();
}

bool SDCardManager::seekWriteFile(uint32_t pos) {
  if (!writeFile.isOpen())
    return false;
  flushWriteBuffer();
  return writeFile.seekSet(pos);
}

void SDCardManager::closeWriteFile() {
  if (writeFile.isOpen()) {
    flushWriteBuffer();
    writeFile.sync();
    writeBufferLength = 0;
    writeFile.close();
  }
}

bool SDCardManager::setWriteFileTimestamp(uint16_t year, uint8_t month, uint8_t day, uint8_t hour, uint8_t minute, uint8_t second) {
  if (!writeFile.isOpen())
    return false;
  return writeFile.timestamp(T_ACCESS | T_CREATE | T_WRITE, year, month, day, hour, minute, second);
}

// ==================== Directory Operations ====================

bool SDCardManager::createDirectory(const char* path) {
  if (!connected) {
    
    return false;
  }

  if (!sdCard.mkdir(path)) {
    
    return false;
  }

  return true;
}

bool SDCardManager::removeDirectory(const char* path) {
  if (!connected) {
    
    return false;
  }

  if (!sdCard.rmdir(path)) {
    
    return false;
  }

  return true;
}

bool SDCardManager::changeDirectory(const char* path) {
  if (!connected) {
    
    return false;
  }

  if (!sdCard.chdir(path)) {
    
    return false;
  }

  return true;
}

// ==================== Helper Methods ====================

bool SDCardManager::hasExtension(const char* filename, const char* extension) {
  if (filename == nullptr || extension == nullptr) {
    return false;
  }

  const char* dot = strrchr(filename, '.');
  if (dot == nullptr) {
    return false;
  }

  // Compare case-insensitively
  char ext1[16], ext2[16];
  strncpy(ext1, dot + 1, sizeof(ext1) - 1);
  ext1[sizeof(ext1) - 1] = '\0';
  
  strncpy(ext2, extension, sizeof(ext2) - 1);
  ext2[sizeof(ext2) - 1] = '\0';
  
  toUpperCase(ext1);
  toUpperCase(ext2);
  
  return strcmp(ext1, ext2) == 0;
}

bool SDCardManager::isHashSidecarFilename(const char* filename) {
  if (filename == nullptr)
    return false;

  size_t filenameLength = strlen(filename);
  size_t suffixLength = strlen(HASH_FILENAME_SUFFIX);

  if (filenameLength < suffixLength)
    return false;

  return strcmp(filename + filenameLength - suffixLength, HASH_FILENAME_SUFFIX) == 0;
}

void SDCardManager::toUpperCase(char* str) {
  while (*str) {
    *str = toupper(*str);
    str++;
  }
}

bool SDCardManager::appendToWriteBuffer(const char* data, size_t dataLength) {
  if (dataLength == 0)
    return true;

  size_t offset = 0;

  while (offset < dataLength) {
    size_t freeSpace = sizeof(writeBuffer) - writeBufferLength;

    if (freeSpace == 0) {
      if (!flushWriteBuffer())
        return false;
      freeSpace = sizeof(writeBuffer);
    }

    size_t chunkLength = dataLength - offset;
    if (chunkLength > freeSpace)
      chunkLength = freeSpace;

    memcpy(writeBuffer + writeBufferLength, data + offset, chunkLength);
    writeBufferLength += chunkLength;
    offset += chunkLength;
  }

  return true;
}

bool SDCardManager::flushWriteBuffer() {
  if (writeBufferLength == 0)
    return true;

  if (writeFile.write(writeBuffer, writeBufferLength) != writeBufferLength)
    return false;

  writeBufferLength = 0;
  return true;
}
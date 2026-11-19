#include "ESP32C3Manager.h"
#include "../Constants.h"
#include "../util/ArrayUtil.h"

namespace bleNameDecoding {

void printBleScanDecodedFields(SerialManager* serialManagerPtr, const char* responseBuffer);

bool isHexDigitChar(char value) {
    return (value >= '0' && value <= '9')
        || (value >= 'a' && value <= 'f')
        || (value >= 'A' && value <= 'F');
}

int hexDigitToInt(char value) {
    if (value >= '0' && value <= '9')
        return value - '0';
    if (value >= 'a' && value <= 'f')
        return 10 + (value - 'a');
    if (value >= 'A' && value <= 'F')
        return 10 + (value - 'A');
    return -1;
}

bool hexPairToByte(char high, char low, char& output) {
    const int highNibble = hexDigitToInt(high);
    const int lowNibble = hexDigitToInt(low);

    if (highNibble < 0 || lowNibble < 0)
        return false;

    output = static_cast<char>((highNibble << 4) | lowNibble);
    return true;
}

bool isPrintableAscii(char value) {
    return value >= 32 && value <= 126;
}

bool extractQuotedField(const char* source, int fieldIndex, char* destination, int destinationSize) {
    if (source == nullptr || destination == nullptr || destinationSize <= 0)
        return false;

    int currentField = 0;
    const char* cursor = source;

    while (*cursor != NULL_CHAR) {
        const char* fieldStart = cursor;
        bool inQuotes = false;

        while (*cursor != NULL_CHAR) {
            if (*cursor == '"')
                inQuotes = !inQuotes;
            else if (*cursor == ',' && !inQuotes)
                break;

            ++cursor;
        }

        if (currentField == fieldIndex) {
            const int fieldLength = static_cast<int>(cursor - fieldStart);
            const int copyLength = fieldLength < destinationSize - 1 ? fieldLength : destinationSize - 1;
            strncpy(destination, fieldStart, copyLength);
            destination[copyLength] = NULL_CHAR;

            if (destination[0] == '"') {
                const int destinationLength = strlen(destination);
                if (destinationLength > 1 && destination[destinationLength - 1] == '"') {
                    memmove(destination, destination + 1, destinationLength - 2);
                    destination[destinationLength - 2] = NULL_CHAR;
                }
            }

            return true;
        }

        ++currentField;
        if (*cursor == ',')
            ++cursor;
    }

    return false;
}

bool extractBleLocalName(const char* advHex, char* nameBuffer, int nameBufferSize) {
    if (advHex == nullptr || nameBuffer == nullptr || nameBufferSize <= 0)
        return false;

    memset(nameBuffer, 0, nameBufferSize);
    const int advHexLength = strlen(advHex);
    int hexIndex = 0;

    while (hexIndex + 1 < advHexLength) {
        char lengthByte = NULL_CHAR;
        if (!hexPairToByte(advHex[hexIndex], advHex[hexIndex + 1], lengthByte))
            return false;

        const int fieldLength = static_cast<unsigned char>(lengthByte);
        hexIndex += 2;

        if (fieldLength == 0)
            break;

        const int fieldHexLength = fieldLength * 2;
        if (hexIndex + fieldHexLength > advHexLength)
            return false;

        char typeByte = NULL_CHAR;
        if (!hexPairToByte(advHex[hexIndex], advHex[hexIndex + 1], typeByte))
            return false;

        const unsigned char fieldType = static_cast<unsigned char>(typeByte);
        if (fieldType == 0x08 || fieldType == 0x09) {
            const int dataByteCount = fieldLength - 1;
            const int copyBytes = dataByteCount < nameBufferSize - 1 ? dataByteCount : nameBufferSize - 1;

            for (int byteIndex = 0; byteIndex < copyBytes; ++byteIndex) {
                char decodedByte = NULL_CHAR;
                const int dataHexIndex = hexIndex + 2 + (byteIndex * 2);
                if (!hexPairToByte(advHex[dataHexIndex], advHex[dataHexIndex + 1], decodedByte))
                    return false;

                nameBuffer[byteIndex] = isPrintableAscii(decodedByte) ? decodedByte : '.';
            }

            nameBuffer[copyBytes] = NULL_CHAR;
            return nameBuffer[0] != NULL_CHAR;
        }

        hexIndex += fieldHexLength;
    }

    return false;
}

bool decodePrintableHexPayload(const char* advHex, char* decodedBuffer, int decodedBufferSize) {
    if (advHex == nullptr || decodedBuffer == nullptr || decodedBufferSize <= 0)
        return false;

    memset(decodedBuffer, 0, decodedBufferSize);
    const int advHexLength = strlen(advHex);
    int decodedIndex = 0;

    for (int index = 0; index + 1 < advHexLength && decodedIndex < decodedBufferSize - 1; index += 2) {
        if (!isHexDigitChar(advHex[index]) || !isHexDigitChar(advHex[index + 1]))
            return false;

        char decodedByte = NULL_CHAR;
        if (!hexPairToByte(advHex[index], advHex[index + 1], decodedByte))
            return false;

        decodedBuffer[decodedIndex] = isPrintableAscii(decodedByte) ? decodedByte : '.';
        ++decodedIndex;
    }

    decodedBuffer[decodedIndex] = NULL_CHAR;
    return decodedIndex > 0;
}

void printBleScanDecodedFields(SerialManager* serialManagerPtr, const char* responseBuffer) {
    if (serialManagerPtr == nullptr || responseBuffer == nullptr)
        return;

    char advData[ESP32C3_RESPONSE_BUFFER_SIZE];
    char scanRspData[ESP32C3_RESPONSE_BUFFER_SIZE];
    char localName[ESP32C3_RESPONSE_BUFFER_SIZE];
    char decodedPayload[ESP32C3_RESPONSE_BUFFER_SIZE];

    memset(advData, 0, sizeof(advData));
    memset(scanRspData, 0, sizeof(scanRspData));
    memset(localName, 0, sizeof(localName));
    memset(decodedPayload, 0, sizeof(decodedPayload));

    if (extractQuotedField(responseBuffer, 2, advData, ESP32C3_RESPONSE_BUFFER_SIZE)) {
        if (extractBleLocalName(advData, localName, ESP32C3_RESPONSE_BUFFER_SIZE)) {
            serialManagerPtr->USBSerialPrint(FROM_ESP32C3);
            serialManagerPtr->USBSerialPrint("BLE advertiser name: ");
            serialManagerPtr->USBSerialPrintln(localName);
        } else if (decodePrintableHexPayload(advData, decodedPayload, ESP32C3_RESPONSE_BUFFER_SIZE)) {
            serialManagerPtr->USBSerialPrint(FROM_ESP32C3);
            serialManagerPtr->USBSerialPrint("BLE adv printable: ");
            serialManagerPtr->USBSerialPrintln(decodedPayload);
        }
    }

    if (extractQuotedField(responseBuffer, 3, scanRspData, ESP32C3_RESPONSE_BUFFER_SIZE) && scanRspData[0] != NULL_CHAR) {
        if (extractBleLocalName(scanRspData, localName, ESP32C3_RESPONSE_BUFFER_SIZE)) {
            serialManagerPtr->USBSerialPrint(FROM_ESP32C3);
            serialManagerPtr->USBSerialPrint("BLE scan response name: ");
            serialManagerPtr->USBSerialPrintln(localName);
        } else if (decodePrintableHexPayload(scanRspData, decodedPayload, ESP32C3_RESPONSE_BUFFER_SIZE)) {
            serialManagerPtr->USBSerialPrint(FROM_ESP32C3);
            serialManagerPtr->USBSerialPrint("BLE scan rsp printable: ");
            serialManagerPtr->USBSerialPrintln(decodedPayload);
        }
    }
}

}

void ESP32C3Manager::init(SerialManager& _serialManager) {

    serialManagerPtr = &_serialManager;
    arrayUtil.clearCharArray(webSocketMessageBuffer, ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE);
    arrayUtil.clearCharArray(responseBuffer, ESP32C3_RESPONSE_BUFFER_SIZE);
    resetWebSocketMessageBuffer();

}

bool ESP32C3Manager::sendATCommand(const char* command) {

    if(serialManagerPtr == nullptr)
        return false;

    if(requestCounter >0)
        return false; //we are already waiting for a response from a previous command, we should wait before sending another one

    if(!serialManagerPtr->ESP32SerialPrintln(command))
        return false;

    clearReadData();//we clear any previous data before sending a new command, to avoid any interference between the previous response and the new one we are going to receive after sending the command
    ++requestCounter;
    resetResponseBuffer();
    arrayUtil.clearCharArray(lastIntermediateResponse, ESP32C3_RESPONSE_BUFFER_SIZE);

    return true;

}

int ESP32C3Manager::checkForResponse() {

    if(serialManagerPtr == nullptr)
        return ESP32C3_RESPONSE_NOT_WAITING;

    if(requestCounter == 0)
        return ESP32C3_RESPONSE_NOT_WAITING; //we are not waiting for any response
    
    int responseChar = -1;

    while((responseChar = serialManagerPtr->readFromESP32()) != -1) {

        if(responseChar == NEWLINE){

            if(getResponseType() == ESP32C3_RESPONSE_TYPE_OK || getResponseType() == ESP32C3_RESPONSE_TYPE_ERROR){

                --requestCounter;
                return ESP32C3_RESPONSE_READY; //a full response has been received, we can process it now

            }else{

                if (responseBufferPos > 0) {
                    arrayUtil.clearCharArray(lastIntermediateResponse, ESP32C3_RESPONSE_BUFFER_SIZE);
                    strncpy(lastIntermediateResponse, responseBuffer, ESP32C3_RESPONSE_BUFFER_SIZE - 1);
                    lastIntermediateResponse[ESP32C3_RESPONSE_BUFFER_SIZE - 1] = NULL_CHAR;
                }

                // Save the intermediate line to pendingBuffer so checkForWebSocketMessage() can process
                // WS events (e.g. +WS_CLOSED:0) that arrive while a command is in-flight.
                if (responseBufferPos > 0 && pendingBufferLen + responseBufferPos + 1 < ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE) {
                    memcpy(pendingBuffer + pendingBufferLen, responseBuffer, responseBufferPos);
                    pendingBufferLen += responseBufferPos;
                    pendingBuffer[pendingBufferLen++] = '\n';
                }
                resetResponseBuffer(); //intermediate line (e.g. "WIFI CONNECTED"), keep reading
                
            }

        }else
        if(responseChar != RETURN){ //we ignore the carriage return characters, since they are not part of the response

            if(responseBufferPos < ESP32C3_RESPONSE_BUFFER_SIZE - 1){
                responseBuffer[responseBufferPos] = responseChar;
                ++responseBufferPos;
            }

        }

    }

    return ESP32C3_RESPONSE_WAITING; //no full response yet

}

bool ESP32C3Manager::getResponse(char *tempResponse, int bufferSize) {

    if(requestCounter == 0){

        arrayUtil.clearCharArray(tempResponse, bufferSize);
        strcat(tempResponse, responseBuffer);
        //we delete the previous response from the buffer, so we can write the new one from the beginning of the buffer
        resetResponseBuffer();
        return true;

    }else
        return false; //we haven't received the full response yet, so we can't return it

}

bool ESP32C3Manager::getLastIntermediateResponse(char *tempResponse, int bufferSize) const {

    if(tempResponse == nullptr || bufferSize <= 0)
        return false;

    tempResponse[0] = NULL_CHAR;

    if(lastIntermediateResponse[0] == NULL_CHAR)
        return false;

    strncpy(tempResponse, lastIntermediateResponse, bufferSize - 1);
    tempResponse[bufferSize - 1] = NULL_CHAR;
    return true;

}

void ESP32C3Manager::resetResponseBuffer() {

    arrayUtil.clearCharArray(responseBuffer, ESP32C3_RESPONSE_BUFFER_SIZE);
    responseBufferPos = 0;

}

int ESP32C3Manager::checkForBLEScanResponse(bool printDebugLogs) {

    if(serialManagerPtr == nullptr)
        return ESP32C3_RESPONSE_NOT_WAITING;

    if(requestCounter == 0)
        return ESP32C3_RESPONSE_NOT_WAITING;

    int responseChar = -1;

    while((responseChar = serialManagerPtr->readFromESP32()) != -1) {

        if(responseChar == NEWLINE) {

            if(strncmp(responseBuffer, ESP32C3_BLE_SCAN_PREFIX, strlen(ESP32C3_BLE_SCAN_PREFIX)) == 0) {
                if(printDebugLogs) {
                    serialManagerPtr->USBSerialPrint(FROM_ESP32C3);
                    serialManagerPtr->USBSerialPrintln(responseBuffer);
                    bleNameDecoding::printBleScanDecodedFields(serialManagerPtr, responseBuffer);
                }
                resetResponseBuffer();
            } else if(strcmp(responseBuffer, ESP32C3_BLE_SCAN_DONE) == 0) {
                if(requestCounter > 0)
                    --requestCounter;
                return ESP32C3_RESPONSE_READY;
            } else if(getResponseType() == ESP32C3_RESPONSE_TYPE_ERROR) {
                if(requestCounter > 0)
                    --requestCounter;
                return ESP32C3_RESPONSE_READY;
            } else if(getResponseType() == ESP32C3_RESPONSE_TYPE_OK) {
                resetResponseBuffer(); // OK can arrive before +BLESCANDONE, so keep waiting for scan results
            } else {
                resetResponseBuffer();
            }

        } else if(responseChar != RETURN) {

            if(responseBufferPos < ESP32C3_RESPONSE_BUFFER_SIZE - 1) {
                responseBuffer[responseBufferPos] = responseChar;
                ++responseBufferPos;
            }

        }

    }

    return ESP32C3_RESPONSE_WAITING;

}

bool ESP32C3Manager::reset() {

    if(serialManagerPtr == nullptr)
        return false;

    bool success = false;

    success = serialManagerPtr->ESP32SerialPrintln(ESP32C3_RESET);
    
    if(success){

        resetResponseBuffer();
        return true;

    }  

    return false;

}

int ESP32C3Manager::getResponseType() {

    if(strcmp(responseBuffer, ESP32C3_RESPONSE_OK) == 0)
        return ESP32C3_RESPONSE_TYPE_OK;
    else if(strcmp(responseBuffer, ESP32C3_RESPONSE_ERROR) == 0)
        return ESP32C3_RESPONSE_TYPE_ERROR;
    else
        return ESP32C3_RESPONSE_TYPE_UNKNOWN;

}

bool ESP32C3Manager::clearReadData() {

    if(serialManagerPtr == nullptr)
        return false;

    while(serialManagerPtr->readFromESP32() != -1){}

    return true;

}

bool ESP32C3Manager::abortPendingCommand() {

    if(serialManagerPtr == nullptr)
        return false;

    // Save any bytes the ESP32-C3 may still be sending into pendingBuffer so that
    // WebSocket events (e.g. +WS_CLOSED:0) arriving during a timed-out command are
    // not silently lost and can be processed by checkForWebSocketMessage() later.
    int c;
    while((c = serialManagerPtr->readFromESP32()) != -1) {
        if (pendingBufferLen < ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE - 1)
            pendingBuffer[pendingBufferLen++] = (char)c;
    }

    requestCounter = 0;
    resetResponseBuffer();

    return true;

}

void ESP32C3Manager::clearPendingBuffer() {

    pendingBufferLen = 0;
    pendingBuffer[0] = '\0';
    resetWebSocketMessageBuffer();

}

// WebSocket receive state-machine states
#define WS_RX_SCAN 0   // scanning for a URC header (+WS_DATA / +WS_CLOSED) or a control line (WIFI DISCONNECT)
#define WS_RX_DATA 1   // reading exactly wsDataRemaining payload bytes of the current +WS_DATA frame

// Returns true once buf contains a full "WS_DATA:<link>,<len>," header (i.e. both commas seen),
// outputting the declared payload length in outLen.
bool ESP32C3Manager::parseWsDataHeader(const char* buf, long* outLen) {

    const char* p = strstr(buf, ESP32C3_WEBSOCKET_MESSAGE_PREFIX); // "WS_DATA:"
    if (p == NULL)
        return false;

    p += strlen(ESP32C3_WEBSOCKET_MESSAGE_PREFIX);

    // link id
    if (*p < '0' || *p > '9') return false;
    while (*p >= '0' && *p <= '9') ++p;
    if (*p != ',') return false;
    ++p;

    // data length
    if (*p < '0' || *p > '9') return false;
    long len = 0;
    while (*p >= '0' && *p <= '9') { len = len * 10 + (*p - '0'); ++p; }
    if (*p != ',') return false; // the closing comma confirms the header is complete

    if (outLen != NULL) *outLen = len;
    return true;

}

// True when webSocketMessageBuffer holds a complete application message: a balanced top-level
// JSON object (string-aware), or a single non-JSON line (which is delivered as-is per frame).
bool ESP32C3Manager::isAssembledWebSocketMessageComplete() {

    int i = 0;
    while (i < webSocketMessageBufferPos && (webSocketMessageBuffer[i] == ' ' || webSocketMessageBuffer[i] == '\t'))
        ++i;

    if (i >= webSocketMessageBufferPos)
        return false; // nothing meaningful yet

    if (webSocketMessageBuffer[i] != '{')
        return true; // non-JSON payload (raw passthrough) — treat each completed frame as one message

    int depth = 0;
    bool inString = false;
    bool escaped = false;
    bool sawOpen = false;

    for (; i < webSocketMessageBufferPos; ++i) {
        char c = webSocketMessageBuffer[i];
        if (escaped) { escaped = false; continue; }
        if (inString) {
            if (c == '\\') escaped = true;
            else if (c == '"') inString = false;
            continue;
        }
        if (c == '"') { inString = true; continue; }
        if (c == '{') { ++depth; sawOpen = true; }
        else if (c == '}') { --depth; }
    }

    return sawOpen && depth == 0 && !inString;

}

// Feeds one received byte into the length-aware state machine. Returns a deliverable message type
// (MOBILE_WEBSOCKET_MESSAGE_RECEIVED / MOBILE_WEBSOCKET_DISCONNECTED / WIFI_DISCONNECTED) when a
// complete message/event is ready, otherwise ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED.
int ESP32C3Manager::feedWebSocketByte(char ch) {

    if (wsReceiveState == WS_RX_DATA) {

        // Read raw payload bytes, regardless of any commas/newlines they may contain.
        if (webSocketMessageBufferPos < ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE - 1)
            webSocketMessageBuffer[webSocketMessageBufferPos++] = ch;
        else
            wsAssemblyOverflow = true; // message too large for the buffer; will be discarded when the frame ends

        if (wsDataRemaining > 0)
            --wsDataRemaining;

        if (wsDataRemaining == 0) {
            // This +WS_DATA frame is complete; resume scanning for the next URC.
            wsReceiveState = WS_RX_SCAN;
            wsHeaderPos = 0;

            if (wsAssemblyOverflow) {
                // Oversized reassembled message — discard it and stay in sync for the next one.
                if (wsRxDebug && serialManagerPtr != nullptr) serialManagerPtr->USBSerialPrintln("[WSRX] OVERFLOW discard");
                resetWebSocketMessageBuffer();
                return ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED;
            }

            // Deliver only once the full application message has been reassembled.
            if (isAssembledWebSocketMessageComplete()) {
                webSocketMessageBuffer[webSocketMessageBufferPos] = NULL_CHAR;
                if (wsRxDebug && serialManagerPtr != nullptr) {
                    serialManagerPtr->USBSerialPrint("[WSRX] complete bytes=");
                    char n[12]; snprintf(n, sizeof(n), "%d", webSocketMessageBufferPos);
                    serialManagerPtr->USBSerialPrintln(n);
                }
                return ESP32C3_MOBILE_WEBSOCKET_MESSAGE_RECEIVED;
            }
            // Otherwise keep accumulating across the following +WS_DATA frames.
            if (wsRxDebug && serialManagerPtr != nullptr) {
                serialManagerPtr->USBSerialPrint("[WSRX] frame done, message still incomplete, accumulated=");
                char n[12]; snprintf(n, sizeof(n), "%d", webSocketMessageBufferPos);
                serialManagerPtr->USBSerialPrintln(n);
            }
        }

        return ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED;

    }

    // WS_RX_SCAN
    if (ch == NEWLINE) {
        // End of a control line (no length prefix): evaluate WS_CLOSED / WIFI DISCONNECT.
        wsHeaderBuf[wsHeaderPos] = NULL_CHAR;
        int result = ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED;
        if (strstr(wsHeaderBuf, ESP32C3_WEBSOCKET_CLOSED_PREFIX) != NULL)
            result = ESP32C3_MOBILE_WEBSOCKET_DISCONNECTED;
        else if (strstr(wsHeaderBuf, ESP32C3_WIFI_DISCONNECT_PREFIX) != NULL)
            result = ESP32C3_WIFI_DISCONNECTED;
        if (wsRxDebug && serialManagerPtr != nullptr && wsHeaderPos > 0) {
            serialManagerPtr->USBSerialPrint("[WSRX] ctrl line: ");
            serialManagerPtr->USBSerialPrintln(wsHeaderBuf);
        }
        wsHeaderPos = 0;
        return result;
    }

    if (ch == RETURN)
        return ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED; // ignore CR while scanning

    // Accumulate header / control-line characters.
    if (wsHeaderPos < (int)sizeof(wsHeaderBuf) - 1) {
        wsHeaderBuf[wsHeaderPos++] = ch;
        wsHeaderBuf[wsHeaderPos] = NULL_CHAR;
    } else {
        // Overflowing scan line is noise (or a payload separator we don't recognise) — restart the scan.
        if (wsRxDebug && serialManagerPtr != nullptr) {
            serialManagerPtr->USBSerialPrint("[WSRX] scan-line overflow, discarding (first 64 chars): ");
            char sample[65]; strncpy(sample, wsHeaderBuf, 64); sample[64] = NULL_CHAR;
            serialManagerPtr->USBSerialPrintln(sample);
        }
        wsHeaderPos = 0;
        return ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED;
    }

    // Did we just complete a "+WS_DATA:<link>,<len>," header?
    long dataLen = 0;
    if (parseWsDataHeader(wsHeaderBuf, &dataLen)) {
        wsHeaderPos = 0;
        if (wsRxDebug && serialManagerPtr != nullptr) {
            serialManagerPtr->USBSerialPrint("[WSRX] +WS_DATA frame header, len=");
            char n[12]; snprintf(n, sizeof(n), "%ld", dataLen);
            serialManagerPtr->USBSerialPrintln(n);
        }
        if (dataLen <= 0) {
            // Empty frame — nothing to read; stay scanning.
            return ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED;
        }
        wsDataRemaining = dataLen;
        wsReceiveState = WS_RX_DATA;
        // Note: webSocketMessageBuffer is intentionally NOT reset here, so the data of this frame
        // is appended to any earlier frames of the same (still-incomplete) application message.
    }

    return ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED;

}

void ESP32C3Manager::setWebSocketRxDebug(bool enabled) {
    wsRxDebug = enabled;
}

int ESP32C3Manager::checkForWebSocketMessage() {

    if(serialManagerPtr == nullptr)
        return ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED;

    int messageChar = -1;
    lastWebSocketMessageBuffered = false;

    // ---- Process any bytes rescued from sendWebSocketMessage / checkForResponse into pendingBuffer ----
    // These are fed through the same length-aware state machine as live serial bytes. A completed
    // message is returned immediately, keeping the unconsumed pending bytes for the next call.
    if (pendingBufferLen > 0) {
        for (int i = 0; i < pendingBufferLen; i++) {
            int messageType = feedWebSocketByte(pendingBuffer[i]);
            if (messageType != ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED) {
                int remaining = pendingBufferLen - (i + 1);
                if (remaining > 0) {
                    memmove(pendingBuffer, pendingBuffer + i + 1, remaining);
                    pendingBuffer[remaining] = '\0';
                    pendingBufferLen = remaining;
                } else {
                    pendingBufferLen = 0;
                }
                lastWebSocketMessageBuffered = true;
                return messageType;
            }
        }
        pendingBufferLen = 0;
    }

    // While an AT command is waiting for OK/ERROR, leave live serial bytes to checkForResponse().
    // That method rescues non-response websocket bytes into pendingBuffer, processed above next call.
    if (requestCounter > 0)
        return ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED;

    while((messageChar = serialManagerPtr->readFromESP32()) != -1){
        int messageType = feedWebSocketByte((char)messageChar);
        if (messageType != ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED)
            return messageType; // return immediately so the caller can process it before more bytes overwrite the buffer
    }

    return ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED;

}

bool ESP32C3Manager::wasLastWebSocketMessageBuffered() const {
    return lastWebSocketMessageBuffered;
}

void ESP32C3Manager::resetWebSocketMessageBuffer() {

    arrayUtil.clearCharArray(webSocketMessageBuffer, ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE);
    webSocketMessageBufferPos = 0;

    // Reset the receive state machine so a partial frame/message never carries across a reset.
    wsReceiveState = WS_RX_SCAN;
    wsDataRemaining = 0;
    wsHeaderPos = 0;
    wsHeaderBuf[0] = NULL_CHAR;
    wsAssemblyOverflow = false;

}

bool ESP32C3Manager::getWebSocketMessagePayload(char *payload, int bufferSize) {

    // checkForWebSocketMessage() has already reassembled the full application message (data only,
    // with +WS_DATA framing stripped) into webSocketMessageBuffer. Copy it out, append a newline so
    // the caller's line-splitter sees a single complete line, then clear the buffer for the next message.

    if (payload == nullptr || bufferSize < 3)
        return false;

    arrayUtil.clearCharArray(payload, bufferSize);

    if (webSocketMessageBufferPos <= 0) {
        resetWebSocketMessageBuffer();
        return false;
    }

    int copyLen = webSocketMessageBufferPos < bufferSize - 2 ? webSocketMessageBufferPos : bufferSize - 2;
    memcpy(payload, webSocketMessageBuffer, copyLen); // memcpy preserves any embedded null bytes
    payload[copyLen] = '\n';
    payload[copyLen + 1] = '\0';
    resetWebSocketMessageBuffer();
    return true;

}

bool ESP32C3Manager::sendWebSocketMessage(const char* message, int linkId) {

    if (serialManagerPtr == nullptr)
        return false;

    int len = strlen(message);
    if (len == 0)
        return false;

    // Send AT+WSSEND=<linkId>,<len> command
    char cmd[48];
    snprintf(cmd, sizeof(cmd), "AT+WSSEND=%d,%d", linkId, len);
    serialManagerPtr->ESP32SerialPrintln(cmd);

    // Wait for '>' prompt.
    // Any non-'>' bytes (e.g. an incoming +WS_DATA: from the other link) are saved to
    // pendingBuffer so checkForWebSocketMessage() can process them on the next iteration.
    unsigned long start = millis();
    bool gotPrompt = false;
    while (millis() - start < ESP32C3_WEBSOCKET_SEND_TIMEOUT_MS) {
        int c = serialManagerPtr->readFromESP32();
        if (c == -1) continue;
        if (c == '>') { gotPrompt = true; break; }
        if (c != RETURN && pendingBufferLen < ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE - 1)
            pendingBuffer[pendingBufferLen++] = (char)c;
    }
    if (!gotPrompt) return false;

    // Send the actual message data
    serialManagerPtr->ESP32SerialPrint(message);

    // Drain until 'SEND OK' or 'SEND FAIL'.
    // Build each line in lineBuf; if it is not an AT response, save it to pendingBuffer
    // (with its newline) so it can be processed later as an incoming WS message.
    char lineBuf[ESP32C3_RESPONSE_BUFFER_SIZE];
    int linePos = 0;
    start = millis();
    while (millis() - start < ESP32C3_WEBSOCKET_SEND_TIMEOUT_MS) {
        int c = serialManagerPtr->readFromESP32();
        if (c == -1) continue;
        if (c == NEWLINE) {
            lineBuf[linePos] = '\0';
            if (strstr(lineBuf, "SEND OK") != NULL)   return true;
            if (strstr(lineBuf, "SEND FAIL") != NULL) return false;
            // Not an AT response — save the line to pendingBuffer
            for (int i = 0; i < linePos && pendingBufferLen < ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE - 1; i++)
                pendingBuffer[pendingBufferLen++] = lineBuf[i];
            if (pendingBufferLen < ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE - 1)
                pendingBuffer[pendingBufferLen++] = '\n';
            linePos = 0;
        } else if (c != RETURN) {
            if (linePos < (int)(sizeof(lineBuf) - 1))
                lineBuf[linePos++] = (char)c;
        }
    }
    return false;

}

int ESP32C3Manager::checkForBLEScanResponseAndCaptureName(bool printDebugLogs, char* serverNameBuffer, int serverNameBufferSize, const char* namePrefix) {

    if(serialManagerPtr == nullptr)
        return ESP32C3_RESPONSE_NOT_WAITING;

    if(requestCounter == 0)
        return ESP32C3_RESPONSE_NOT_WAITING;

    const int prefixLen = (namePrefix != nullptr) ? static_cast<int>(strlen(namePrefix)) : 0;
    int responseChar = -1;

    while((responseChar = serialManagerPtr->readFromESP32()) != -1) {

        if(responseChar == NEWLINE) {

            if(strncmp(responseBuffer, ESP32C3_BLE_SCAN_PREFIX, strlen(ESP32C3_BLE_SCAN_PREFIX)) == 0) {

                // Try to extract the BLE local name and capture the server name suffix
                char advData[ESP32C3_RESPONSE_BUFFER_SIZE];
                char localName[ESP32C3_RESPONSE_BUFFER_SIZE];

                memset(advData, 0, sizeof(advData));
                memset(localName, 0, sizeof(localName));

                auto tryCaptureFromField = [&](int fieldIndex) {
                    if(serverNameBuffer[0] != NULL_CHAR) return; // already captured
                    if(!bleNameDecoding::extractQuotedField(responseBuffer, fieldIndex, advData, ESP32C3_RESPONSE_BUFFER_SIZE)) return;
                    if(advData[0] == NULL_CHAR) return;
                    if(!bleNameDecoding::extractBleLocalName(advData, localName, ESP32C3_RESPONSE_BUFFER_SIZE)) return;
                    if(prefixLen > 0 && strncmp(localName, namePrefix, prefixLen) != 0) return;
                    const char* suffix = localName + prefixLen;
                    int copyLen = static_cast<int>(strlen(suffix));
                    if(copyLen >= serverNameBufferSize) copyLen = serverNameBufferSize - 1;
                    strncpy(serverNameBuffer, suffix, copyLen);
                    serverNameBuffer[copyLen] = NULL_CHAR;
                };

                tryCaptureFromField(2); // advData field
                tryCaptureFromField(3); // scanRspData field

                if(printDebugLogs) {
                    serialManagerPtr->USBSerialPrint(FROM_ESP32C3);
                    serialManagerPtr->USBSerialPrintln(responseBuffer);
                    bleNameDecoding::printBleScanDecodedFields(serialManagerPtr, responseBuffer);
                }

                resetResponseBuffer();

            } else if(strcmp(responseBuffer, ESP32C3_BLE_SCAN_DONE) == 0) {
                if(requestCounter > 0)
                    --requestCounter;
                return ESP32C3_RESPONSE_READY;
            } else if(getResponseType() == ESP32C3_RESPONSE_TYPE_ERROR) {
                if(requestCounter > 0)
                    --requestCounter;
                return ESP32C3_RESPONSE_READY;
            } else if(getResponseType() == ESP32C3_RESPONSE_TYPE_OK) {
                resetResponseBuffer();
            } else {
                resetResponseBuffer();
            }

        } else if(responseChar != RETURN) {

            if(responseBufferPos < ESP32C3_RESPONSE_BUFFER_SIZE - 1) {
                responseBuffer[responseBufferPos] = responseChar;
                ++responseBufferPos;
            }

        }

    }

    return ESP32C3_RESPONSE_WAITING;

}
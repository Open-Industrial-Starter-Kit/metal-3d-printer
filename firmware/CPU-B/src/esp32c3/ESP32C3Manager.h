#ifndef ESP32C3_MANAGER_H
#define ESP32C3_MANAGER_H

#include "../serial/SerialManager.h"
#include "../Constants.h"
#include "../util/ArrayUtil.h"

class ESP32C3Manager {

public:
    ESP32C3Manager() : serialManagerPtr(nullptr), requestCounter(0), pendingBufferLen(0) {};
    void init(SerialManager& _serialManager);

    bool sendATCommand(const char* command);
    int checkForResponse();  
    bool getResponse(char *tempResponse, int bufferSize);  
    bool getLastIntermediateResponse(char *tempResponse, int bufferSize) const;
    int getResponseType(); //this function will check the response buffer and return an integer representing the type of response received
    bool reset();
    bool clearReadData();
    bool abortPendingCommand(); // drains the ESP32-C3 serial RX buffer and resets requestCounter; call this when abandoning a command that never received a response
    void clearPendingBuffer();   // discards any bytes saved in the pending event buffer; call after a full re-init to avoid stale WIFI DISCONNECT etc. triggering reconnect logic
    
    int checkForWebSocketMessage(); // reads/reassembles incoming +WS_DATA frames; returns a deliverable message/event type when one completes
    bool wasLastWebSocketMessageBuffered() const;
    void setWebSocketRxDebug(bool enabled); // when true, logs frame headers / completion / control lines of the WS receive state machine to USB
    bool getWebSocketMessagePayload(char *payload, int bufferSize); //copies the reassembled message payload (set by checkForWebSocketMessage) into the provided buffer with a trailing newline
    bool sendWebSocketMessage(const char* message, int linkId = 0); //sends a message to the connected websocket client via AT+WSSEND
    int checkForBLEScanResponse(bool printDebugLogs); //like checkForResponse() but can optionally print intermediate +BLESCAN: lines to USB serial
    int checkForBLEScanResponseAndCaptureName(bool printDebugLogs, char* serverNameBuffer, int serverNameBufferSize, const char* namePrefix); //like checkForBLEScanResponse() but also extracts the server name suffix from the first BLE advertiser whose name starts with namePrefix

private:

    SerialManager *serialManagerPtr;
    int requestCounter;
    char responseBuffer[ESP32C3_RESPONSE_BUFFER_SIZE];
    char webSocketMessageBuffer[ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE];
    char lastIntermediateResponse[ESP32C3_RESPONSE_BUFFER_SIZE];
    // Bytes rescued from sendWebSocketMessage blocking loops, processed next by checkForWebSocketMessage
    char pendingBuffer[ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE];
    int pendingBufferLen;
    bool lastWebSocketMessageBuffered = false;

    int responseBufferPos;
    int webSocketMessageBufferPos;
    ArrayUtil arrayUtil;

    // ---- WebSocket receive state machine ----
    // The +WS_DATA:<link>,<len>,<data> URC is length-prefixed and a single application
    // message (e.g. a large file-chunk JSON) can be split by the ESP32-C3 across several
    // +WS_DATA frames. The state machine reads exactly <len> data bytes per frame (ignoring
    // any newlines inside the data) and reassembles consecutive frames in webSocketMessageBuffer
    // until the application message is complete (a balanced JSON object, or a single non-JSON line).
    int wsReceiveState;        // WS_RX_SCAN (looking for a URC header / control line) or WS_RX_DATA (reading payload bytes)
    long wsDataRemaining;      // bytes still to read for the current +WS_DATA frame
    char wsHeaderBuf[ESP32C3_RESPONSE_BUFFER_SIZE]; // accumulates the header / control line while scanning
    int  wsHeaderPos;
    bool wsAssemblyOverflow;   // set when the reassembled message would exceed webSocketMessageBuffer
    bool wsRxDebug = false;     // diagnostic logging toggle for the WS receive state machine

    void resetResponseBuffer();
    void resetWebSocketMessageBuffer();

    int feedWebSocketByte(char ch); // feeds one received byte into the state machine; returns a deliverable message type when one completes, else ESP32C3_WEBSOCKET_MESSAGE_NOT_RECEIVED
    bool parseWsDataHeader(const char* buf, long* outLen); // true once a full "WS_DATA:<link>,<len>," header is present in buf; outputs <len>
    bool isAssembledWebSocketMessageComplete(); // true when webSocketMessageBuffer holds a complete application message

};

#endif // ESP32C3_MANAGER_H
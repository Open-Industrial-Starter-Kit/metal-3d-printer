#include "Benchmark.h"
#include "../Constants.h"



void Benchmark::setStartTime(unsigned long time) {
    startTime = time;
}

void Benchmark::setStopTime(unsigned long time) {
    stopTime = time;
}

unsigned long Benchmark::getElapsedTime(int minimumTime) {
    
    if(stopTime - startTime > minimumTime)
        return stopTime - startTime;
    else
        return -1;
}
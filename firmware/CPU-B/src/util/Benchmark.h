#ifndef BENCHMARK_H
#define BENCHMARK_H

class Benchmark {
public:
    void setStartTime(unsigned long time);
    void setStopTime(unsigned long time);
    unsigned long getElapsedTime(int minimumTime);

private:
    unsigned long startTime = 0.0;
    unsigned long stopTime = 0.0;
};

#endif // BENCHMARK_H
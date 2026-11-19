#ifndef GCODE_BOUNDS_UTIL_H
#define GCODE_BOUNDS_UTIL_H

#include <Arduino.h>

class GCodeBoundsUtil {
public:
  GCodeBoundsUtil();

  static bool isMetadataLine(const char* line);
  static bool parseMetadataLine(const char* line, float* sizeX, float* sizeY, float* sizeZ, unsigned long* durationSeconds = nullptr);
  // Parses the work-coordinate bounding box (MINX/MAXX/MINY/MAXY) from a bounds metadata
  // line. Returns false for a non-metadata line or EMPTY=1 metadata (no MIN/MAX fields).
  static bool parseMetadataBounds(const char* line, float* minX, float* maxX, float* minY, float* maxY);

  void reset();
  void processLine(const char* line);

  bool hasBounds() const;
  bool isExact() const;
  bool isMetricUnits() const;
  float getEstimatedDurationSeconds() const;
  unsigned long getEstimatedDurationSecondsRounded() const;

  float getMinX() const;
  float getMaxX() const;
  float getMinY() const;
  float getMaxY() const;
  float getMinZ() const;
  float getMaxZ() const;

private:
  enum MotionMode {
    MOTION_NONE,
    MOTION_G0,
    MOTION_G1,
    MOTION_G2,
    MOTION_G3,
    MOTION_OTHER
  };

  enum MotionPlane {
    PLANE_XY,
    PLANE_XZ,
    PLANE_YZ
  };

  bool absoluteMode;
  bool metricUnits;
  bool boundsAvailable;
  bool exactBounds;
  float currentX;
  float currentY;
  float currentZ;
  float minX;
  float maxX;
  float minY;
  float maxY;
  float minZ;
  float maxZ;
  MotionMode lastMotionMode;
  MotionPlane activePlane;
  float currentFeedRateMmPerMin;
  float estimatedDurationSeconds;

  // Arc geometry projected onto the active plane. Axis A/B are the two in-plane axes
  // (X/Y for G17, X/Z for G18, Y/Z for G19); "third" is the axis normal to it, which a
  // helix travels along linearly.
  struct ArcGeometry {
    bool valid;        // false when no usable centre is given (R-format arc, degenerate radius)
    float centerA;
    float centerB;
    float radius;
    float startAngle;
    float sweepAngle;  // signed, in the direction of travel: negative = G2 (CW), positive = G3 (CCW)
    float startThird;
    float endThird;
  };

  void updateBounds(float x, float y, float z);
  // Feeds a point given in active-plane coordinates back into the XYZ bounding box.
  void updateBoundsFromPlanePoint(float axisA, float axisB, float axisThird);
  // Grows the bounding box by the extreme points the arc actually sweeps through, so a
  // curve counts for its real extent and not just for its endpoint.
  void updateBoundsForArc(
    MotionMode motionMode,
    float targetX,
    float targetY,
    float targetZ,
    bool hasI,
    bool hasJ,
    bool hasK,
    float offsetI,
    float offsetJ,
    float offsetK
  );
  bool resolveArcGeometry(
    MotionMode motionMode,
    float targetX,
    float targetY,
    float targetZ,
    bool hasI,
    bool hasJ,
    bool hasK,
    float offsetI,
    float offsetJ,
    float offsetK,
    ArcGeometry* geometry
  ) const;
  void stripCommentsAndNormalize(const char* line, char* output, size_t outputSize);
  float computeLinearDistance(float targetX, float targetY, float targetZ) const;
  float computeArcDistance(
    MotionMode motionMode,
    float targetX,
    float targetY,
    float targetZ,
    bool hasI,
    bool hasJ,
    bool hasK,
    float offsetI,
    float offsetJ,
    float offsetK
  ) const;
};

#endif
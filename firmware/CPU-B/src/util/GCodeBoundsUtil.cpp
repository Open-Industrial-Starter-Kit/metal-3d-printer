#include "GCodeBoundsUtil.h"

#include "../Config.h"

#include <ctype.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

GCodeBoundsUtil::GCodeBoundsUtil() {
  reset();
}

bool GCodeBoundsUtil::isMetadataLine(const char* line) {
  static const char metadataPrefix[] = ";MINICNC_BOUNDS";

  return line != nullptr && strncmp(line, metadataPrefix, strlen(metadataPrefix)) == 0;
}

bool GCodeBoundsUtil::parseMetadataLine(const char* line, float* sizeX, float* sizeY, float* sizeZ, unsigned long* durationSeconds) {
  if (sizeX == nullptr || sizeY == nullptr || sizeZ == nullptr)
    return false;

  *sizeX = 0.0f;
  *sizeY = 0.0f;
  *sizeZ = 0.0f;
  if (durationSeconds != nullptr)
    *durationSeconds = 0;

  if (!isMetadataLine(line))
    return false;

  const char* sizeXPtr = strstr(line, "SIZEX=");
  const char* sizeYPtr = strstr(line, "SIZEY=");
  const char* sizeZPtr = strstr(line, "SIZEZ=");

  if (sizeXPtr == nullptr || sizeYPtr == nullptr || sizeZPtr == nullptr)
    return false;

  *sizeX = strtof(sizeXPtr + 6, nullptr);
  *sizeY = strtof(sizeYPtr + 6, nullptr);
  *sizeZ = strtof(sizeZPtr + 6, nullptr);

  if (durationSeconds != nullptr) {
    const char* durationPtr = strstr(line, "DURATION_SEC=");
    if (durationPtr != nullptr)
      *durationSeconds = strtoul(durationPtr + 13, nullptr, 10);
  }

  return true;
}

bool GCodeBoundsUtil::parseMetadataBounds(const char* line, float* minX, float* maxX, float* minY, float* maxY) {
  if (minX == nullptr || maxX == nullptr || minY == nullptr || maxY == nullptr)
    return false;

  *minX = 0.0f;
  *maxX = 0.0f;
  *minY = 0.0f;
  *maxY = 0.0f;

  if (!isMetadataLine(line))
    return false;

  const char* minXPtr = strstr(line, "MINX=");
  const char* maxXPtr = strstr(line, "MAXX=");
  const char* minYPtr = strstr(line, "MINY=");
  const char* maxYPtr = strstr(line, "MAXY=");

  // EMPTY=1 metadata (a file with no motion) carries no MIN/MAX fields: nothing to frame.
  if (minXPtr == nullptr || maxXPtr == nullptr || minYPtr == nullptr || maxYPtr == nullptr)
    return false;

  *minX = strtof(minXPtr + 5, nullptr);  // strlen("MINX=") == 5
  *maxX = strtof(maxXPtr + 5, nullptr);
  *minY = strtof(minYPtr + 5, nullptr);
  *maxY = strtof(maxYPtr + 5, nullptr);

  return true;
}

void GCodeBoundsUtil::reset() {
  absoluteMode = true;
  metricUnits = true;
  boundsAvailable = false;
  exactBounds = true;
  currentX = 0.0f;
  currentY = 0.0f;
  currentZ = 0.0f;
  minX = 0.0f;
  maxX = 0.0f;
  minY = 0.0f;
  maxY = 0.0f;
  minZ = 0.0f;
  maxZ = 0.0f;
  lastMotionMode = MOTION_NONE;
  activePlane = PLANE_XY;
  currentFeedRateMmPerMin = 0.0f;
  estimatedDurationSeconds = 0.0f;
}

void GCodeBoundsUtil::processLine(const char* line) {
  if (line == nullptr || line[0] == '\0')
    return;

  char normalizedLine[256];
  stripCommentsAndNormalize(line, normalizedLine, sizeof(normalizedLine));

  if (normalizedLine[0] == '\0')
    return;

  bool hasX = false;
  bool hasY = false;
  bool hasZ = false;
  bool hasI = false;
  bool hasJ = false;
  bool hasK = false;
  bool hasFeed = false;
  float parsedX = 0.0f;
  float parsedY = 0.0f;
  float parsedZ = 0.0f;
  float parsedI = 0.0f;
  float parsedJ = 0.0f;
  float parsedK = 0.0f;
  float parsedFeed = 0.0f;
  MotionMode lineMotionMode = MOTION_NONE;
  bool hasG53 = false;
  bool hasUnsupportedMotion = false;
  bool hasUnsupportedCoordinateTransform = false;

  char* cursor = normalizedLine;
  while (*cursor != '\0') {
    while (*cursor != '\0' && isspace(static_cast<unsigned char>(*cursor)))
      ++cursor;

    if (*cursor == '\0')
      break;

    if (!isalpha(static_cast<unsigned char>(*cursor))) {
      ++cursor;
      continue;
    }

    char token = *cursor;
    ++cursor;

    char* valueStart = cursor;
    while (*cursor != '\0' && !isalpha(static_cast<unsigned char>(*cursor)) && !isspace(static_cast<unsigned char>(*cursor)))
      ++cursor;

    size_t valueLength = static_cast<size_t>(cursor - valueStart);
    if (valueLength == 0)
      continue;

    char valueBuffer[24];
    if (valueLength >= sizeof(valueBuffer))
      valueLength = sizeof(valueBuffer) - 1;

    memcpy(valueBuffer, valueStart, valueLength);
    valueBuffer[valueLength] = '\0';

    if (token == 'G') {
      long gCode = strtol(valueBuffer, nullptr, 10);

      if (gCode == 0)
        lineMotionMode = MOTION_G0;
      else if (gCode == 1)
        lineMotionMode = MOTION_G1;
      else if (gCode == 2)
        lineMotionMode = MOTION_G2;
      else if (gCode == 3)
        lineMotionMode = MOTION_G3;
      else if (gCode == 17)
        activePlane = PLANE_XY;
      else if (gCode == 18)
        activePlane = PLANE_XZ;
      else if (gCode == 19)
        activePlane = PLANE_YZ;
      else if (gCode == 20)
        metricUnits = false;
      else if (gCode == 21)
        metricUnits = true;
      else if (gCode == 53)
        hasG53 = true;
      else if (gCode == 90)
        absoluteMode = true;
      else if (gCode == 91)
        absoluteMode = false;
      else if (gCode == 92)
        hasUnsupportedCoordinateTransform = true;
    } else if (token == 'X') {
      parsedX = strtof(valueBuffer, nullptr);
      hasX = true;
    } else if (token == 'Y') {
      parsedY = strtof(valueBuffer, nullptr);
      hasY = true;
    } else if (token == 'Z') {
      parsedZ = strtof(valueBuffer, nullptr);
      hasZ = true;
    } else if (token == 'I') {
      parsedI = strtof(valueBuffer, nullptr);
      hasI = true;
    } else if (token == 'J') {
      parsedJ = strtof(valueBuffer, nullptr);
      hasJ = true;
    } else if (token == 'K') {
      parsedK = strtof(valueBuffer, nullptr);
      hasK = true;
    } else if (token == 'F') {
      parsedFeed = strtof(valueBuffer, nullptr);
      hasFeed = true;
    }
  }

  if (lineMotionMode != MOTION_NONE)
    lastMotionMode = lineMotionMode;

  // Arcs are measured exactly (see updateBoundsForArc), which clears itself to inexact only
  // when the arc centre cannot be resolved.
  if (hasUnsupportedMotion || hasUnsupportedCoordinateTransform || hasG53)
    exactBounds = false;

  if (hasFeed) {
    currentFeedRateMmPerMin = metricUnits ? parsedFeed : (parsedFeed * 25.4f);
  }

  MotionMode effectiveMotionMode = lineMotionMode != MOTION_NONE ? lineMotionMode : lastMotionMode;
  if (!hasX && !hasY && !hasZ)
    return;

  if (!metricUnits) {
    const float inchesToMm = 25.4f;
    if (hasX)
      parsedX *= inchesToMm;
    if (hasY)
      parsedY *= inchesToMm;
    if (hasZ)
      parsedZ *= inchesToMm;
    if (hasI)
      parsedI *= inchesToMm;
    if (hasJ)
      parsedJ *= inchesToMm;
    if (hasK)
      parsedK *= inchesToMm;
  }

  float targetX = currentX;
  float targetY = currentY;
  float targetZ = currentZ;

  if (absoluteMode) {
    if (hasX)
      targetX = parsedX;
    if (hasY)
      targetY = parsedY;
    if (hasZ)
      targetZ = parsedZ;
  } else {
    if (hasX)
      targetX += parsedX;
    if (hasY)
      targetY += parsedY;
    if (hasZ)
      targetZ += parsedZ;
  }

  if (!hasG53 && !hasUnsupportedCoordinateTransform) {
    float segmentDurationSeconds = 0.0f;

    if (effectiveMotionMode == MOTION_G0) {
      float distance = computeLinearDistance(targetX, targetY, targetZ);
      segmentDurationSeconds = (distance * 60.0f) / GCODE_RAPID_FEED_RATE_MM_PER_MIN;
    } else if (effectiveMotionMode == MOTION_G1) {
      // Uses last received feed rate even when this line has no F word
      if (currentFeedRateMmPerMin > 0.0f) {
        float distance = computeLinearDistance(targetX, targetY, targetZ);
        segmentDurationSeconds = (distance * 60.0f) / currentFeedRateMmPerMin;
      }
    } else if (effectiveMotionMode == MOTION_G2 || effectiveMotionMode == MOTION_G3) {
      if (currentFeedRateMmPerMin > 0.0f) {
        float distance = computeArcDistance(effectiveMotionMode, targetX, targetY, targetZ, hasI, hasJ, hasK, parsedI, parsedJ, parsedK);
        segmentDurationSeconds = (distance * 60.0f) / currentFeedRateMmPerMin;
      }
    }

    if (segmentDurationSeconds > 0.0f)
      estimatedDurationSeconds += segmentDurationSeconds;

  } else if (hasG53) {
    // G53 (machine-coordinate) moves: time using the last received feed rate
    if (currentFeedRateMmPerMin > 0.0f) {
      float distance = computeLinearDistance(targetX, targetY, targetZ);
      float segmentDurationSeconds = (distance * 60.0f) / currentFeedRateMmPerMin;
      if (segmentDurationSeconds > 0.0f)
        estimatedDurationSeconds += segmentDurationSeconds;
    }
  }

  const bool skipBounds = hasG53 || hasUnsupportedMotion || hasUnsupportedCoordinateTransform;

  // Arcs: the endpoint alone under-reports the extent (a curve bulges past both of its ends),
  // so the swept extremes are added before the endpoint. Must run while currentX/Y/Z still
  // hold the start of the move.
  if ((effectiveMotionMode == MOTION_G2 || effectiveMotionMode == MOTION_G3) && !skipBounds) {
    updateBoundsForArc(effectiveMotionMode, targetX, targetY, targetZ, hasI, hasJ, hasK, parsedI, parsedJ, parsedK);
    currentX = targetX;
    currentY = targetY;
    currentZ = targetZ;
    updateBounds(currentX, currentY, currentZ);
    return;
  }

  if ((effectiveMotionMode != MOTION_G0 && effectiveMotionMode != MOTION_G1) || skipBounds) {
    currentX = targetX;
    currentY = targetY;
    currentZ = targetZ;
    return;
  }

  currentX = targetX;
  currentY = targetY;
  currentZ = targetZ;
  updateBounds(currentX, currentY, currentZ);
}

bool GCodeBoundsUtil::hasBounds() const {
  return boundsAvailable;
}

bool GCodeBoundsUtil::isExact() const {
  return exactBounds && boundsAvailable;
}

bool GCodeBoundsUtil::isMetricUnits() const {
  return metricUnits;
}

float GCodeBoundsUtil::getEstimatedDurationSeconds() const {
  return estimatedDurationSeconds;
}

unsigned long GCodeBoundsUtil::getEstimatedDurationSecondsRounded() const {
  return (estimatedDurationSeconds <= 0.0f) ? 0UL : static_cast<unsigned long>(lroundf(estimatedDurationSeconds));
}

float GCodeBoundsUtil::getMinX() const {
  return minX;
}

float GCodeBoundsUtil::getMaxX() const {
  return maxX;
}

float GCodeBoundsUtil::getMinY() const {
  return minY;
}

float GCodeBoundsUtil::getMaxY() const {
  return maxY;
}

float GCodeBoundsUtil::getMinZ() const {
  return minZ;
}

float GCodeBoundsUtil::getMaxZ() const {
  return maxZ;
}

void GCodeBoundsUtil::updateBounds(float x, float y, float z) {
  if (!boundsAvailable) {
    minX = maxX = x;
    minY = maxY = y;
    minZ = maxZ = z;
    boundsAvailable = true;
    return;
  }

  if (x < minX)
    minX = x;
  if (x > maxX)
    maxX = x;
  if (y < minY)
    minY = y;
  if (y > maxY)
    maxY = y;
  if (z < minZ)
    minZ = z;
  if (z > maxZ)
    maxZ = z;
}

void GCodeBoundsUtil::updateBoundsFromPlanePoint(float axisA, float axisB, float axisThird) {
  if (activePlane == PLANE_XY)
    updateBounds(axisA, axisB, axisThird);
  else if (activePlane == PLANE_XZ)
    updateBounds(axisA, axisThird, axisB);
  else
    updateBounds(axisThird, axisA, axisB);
}

void GCodeBoundsUtil::updateBoundsForArc(
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
) {
  ArcGeometry geometry;

  if (!resolveArcGeometry(motionMode, targetX, targetY, targetZ, hasI, hasJ, hasK, offsetI, offsetJ, offsetK, &geometry)) {
    // Centre unknown (R-format or degenerate radius): only the endpoint can be trusted, so
    // the bulge of this arc is missing from the box.
    exactBounds = false;
    return;
  }

  // On a circle the extremes lie at the four cardinal angles, so an arc only widens the box
  // where it actually sweeps through one of them. The endpoint is added by the caller.
  for (int quadrant = 0; quadrant < 4; ++quadrant) {
    const float cardinalAngle = static_cast<float>(quadrant) * (static_cast<float>(PI) / 2.0f);

    // Angular distance from the start, measured in the direction of travel.
    float delta = cardinalAngle - geometry.startAngle;

    if (geometry.sweepAngle > 0.0f) {
      while (delta < 0.0f)
        delta += 2.0f * PI;
      while (delta >= 2.0f * PI)
        delta -= 2.0f * PI;

      if (delta > geometry.sweepAngle)
        continue;
    } else {
      while (delta > 0.0f)
        delta -= 2.0f * PI;
      while (delta <= -2.0f * PI)
        delta += 2.0f * PI;

      if (delta < geometry.sweepAngle)
        continue;
    }

    // Where along the arc this extreme falls, so a helix gets the matching third-axis height.
    const float fraction = (fabsf(geometry.sweepAngle) > 0.0001f) ? (delta / geometry.sweepAngle) : 0.0f;

    updateBoundsFromPlanePoint(
      geometry.centerA + (geometry.radius * cosf(cardinalAngle)),
      geometry.centerB + (geometry.radius * sinf(cardinalAngle)),
      geometry.startThird + (fraction * (geometry.endThird - geometry.startThird))
    );
  }
}

void GCodeBoundsUtil::stripCommentsAndNormalize(const char* line, char* output, size_t outputSize) {
  if (outputSize == 0)
    return;

  bool insideParenthesisComment = false;
  size_t outputIndex = 0;

  for (size_t i = 0; line[i] != '\0' && outputIndex < outputSize - 1; ++i) {
    char currentChar = line[i];

    if (currentChar == ';')
      break;

    if (currentChar == '(') {
      insideParenthesisComment = true;
      continue;
    }

    if (currentChar == ')' && insideParenthesisComment) {
      insideParenthesisComment = false;
      continue;
    }

    if (insideParenthesisComment)
      continue;

    output[outputIndex++] = static_cast<char>(toupper(static_cast<unsigned char>(currentChar)));
  }

  output[outputIndex] = '\0';
}

float GCodeBoundsUtil::computeLinearDistance(float targetX, float targetY, float targetZ) const {
  const float deltaX = targetX - currentX;
  const float deltaY = targetY - currentY;
  const float deltaZ = targetZ - currentZ;
  return sqrtf((deltaX * deltaX) + (deltaY * deltaY) + (deltaZ * deltaZ));
}

bool GCodeBoundsUtil::resolveArcGeometry(
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
) const {
  if (geometry == nullptr)
    return false;

  geometry->valid = false;

  float startAxisA = 0.0f;
  float startAxisB = 0.0f;
  float endAxisA = 0.0f;
  float endAxisB = 0.0f;
  float offsetA = 0.0f;
  float offsetB = 0.0f;
  bool hasOffsetA = false;
  bool hasOffsetB = false;
  float startThird = 0.0f;
  float endThird = 0.0f;

  if (activePlane == PLANE_XY) {
    startAxisA = currentX;
    startAxisB = currentY;
    endAxisA = targetX;
    endAxisB = targetY;
    offsetA = offsetI;
    offsetB = offsetJ;
    hasOffsetA = hasI;
    hasOffsetB = hasJ;
    startThird = currentZ;
    endThird = targetZ;
  } else if (activePlane == PLANE_XZ) {
    startAxisA = currentX;
    startAxisB = currentZ;
    endAxisA = targetX;
    endAxisB = targetZ;
    offsetA = offsetI;
    offsetB = offsetK;
    hasOffsetA = hasI;
    hasOffsetB = hasK;
    startThird = currentY;
    endThird = targetY;
  } else {
    startAxisA = currentY;
    startAxisB = currentZ;
    endAxisA = targetY;
    endAxisB = targetZ;
    offsetA = offsetJ;
    offsetB = offsetK;
    hasOffsetA = hasJ;
    hasOffsetB = hasK;
    startThird = currentX;
    endThird = targetX;
  }

  // No centre offset on the active plane (e.g. an R-format arc): the geometry is unknown.
  if (!hasOffsetA && !hasOffsetB)
    return false;

  const float centerAxisA = startAxisA + (hasOffsetA ? offsetA : 0.0f);
  const float centerAxisB = startAxisB + (hasOffsetB ? offsetB : 0.0f);
  const float radiusStart = hypotf(startAxisA - centerAxisA, startAxisB - centerAxisB);
  const float radiusEnd = hypotf(endAxisA - centerAxisA, endAxisB - centerAxisB);

  if (radiusStart <= 0.0001f && radiusEnd <= 0.0001f)
    return false;

  float radius = radiusStart;

  if (radiusEnd > 0.0001f)
    radius = (radiusStart + radiusEnd) * 0.5f;

  const float startAngle = atan2f(startAxisB - centerAxisB, startAxisA - centerAxisA);
  const float endAngle = atan2f(endAxisB - centerAxisB, endAxisA - centerAxisA);

  // Signed sweep in the direction of travel, so the arc bounds can tell which part of the
  // circle is actually traversed: negative for G2 (clockwise), positive for G3.
  float sweep = endAngle - startAngle;

  if (motionMode == MOTION_G2) {
    while (sweep >= 0.0f)
      sweep -= 2.0f * PI;
  } else {
    while (sweep <= 0.0f)
      sweep += 2.0f * PI;
  }

  // Coincident start/end with a vanishing sweep is a full circle, not a zero-length move.
  if (fabsf(sweep) < 0.0001f && fabsf(endAxisA - startAxisA) < 0.0001f && fabsf(endAxisB - startAxisB) < 0.0001f)
    sweep = (motionMode == MOTION_G2) ? (-2.0f * PI) : (2.0f * PI);

  geometry->valid = true;
  geometry->centerA = centerAxisA;
  geometry->centerB = centerAxisB;
  geometry->radius = radius;
  geometry->startAngle = startAngle;
  geometry->sweepAngle = sweep;
  geometry->startThird = startThird;
  geometry->endThird = endThird;

  return true;
}

float GCodeBoundsUtil::computeArcDistance(
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
) const {
  ArcGeometry geometry;

  if (!resolveArcGeometry(motionMode, targetX, targetY, targetZ, hasI, hasJ, hasK, offsetI, offsetJ, offsetK, &geometry))
    return computeLinearDistance(targetX, targetY, targetZ);

  const float planarLength = geometry.radius * fabsf(geometry.sweepAngle);
  const float helixDelta = geometry.endThird - geometry.startThird;

  if (fabsf(helixDelta) <= 0.0001f)
    return planarLength;

  return sqrtf((planarLength * planarLength) + (helixDelta * helixDelta));
}

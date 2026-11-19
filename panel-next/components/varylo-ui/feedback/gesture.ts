/**
 * Whether a swipe/drag released after `distance` px (towards dismissal) in `elapsedMs`
 * should dismiss: either it went far enough, or it was a quick flick over a short distance.
 */
export function isDismissGesture(distance: number, elapsedMs: number, threshold: number, flickVelocity: number) {
  if (distance >= threshold) return true;
  return distance > 16 && distance / Math.max(1, elapsedMs) > flickVelocity;
}

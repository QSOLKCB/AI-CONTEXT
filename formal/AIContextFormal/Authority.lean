import AIContextFormal.Core

namespace AIContextFormal

def isReviewAuthority : Actor → Bool
  | .human => true
  | .policy => true
  | _ => false

def canApply (actor : Actor) (decision : ReviewDecision) : Bool :=
  isReviewAuthority actor && decision == .approve

def sensitivityRank : Sensitivity → Nat
  | .public => 0
  | .private => 1
  | .restricted => 2
  | .secret => 3

def canReclassify (old new : Sensitivity) : Bool :=
  decide (sensitivityRank old ≤ sensitivityRank new)

def canonicalEligible (sensitivity : Sensitivity) : Bool :=
  !(sensitivity == .secret)

end AIContextFormal

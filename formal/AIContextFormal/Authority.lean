import AIContextFormal.Core

namespace AIContextFormal

inductive SourceImporter where
  | providerAdapter
  | repositoryAdapter
  | documentAdapter
  | evidenceAdapter
  deriving Repr, DecidableEq, BEq

/-- Import adapters may move raw source material into staging only. Canonical
memory requires the separate curation/review/application authority path. -/
inductive ImportTransition : SourceImporter → TrustZone → TrustZone → Prop where
  | stage (importer : SourceImporter) :
      ImportTransition importer .rawVault .staging

inductive ContentSafety where
  | secretFree
  | secretShaped
  deriving Repr, DecidableEq, BEq

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

/-- Canonical eligibility requires both an allowed classification and content
that is independently free of secret-shaped material. A misleading public or
private label cannot make secret-shaped content eligible. -/
def canonicalEligible (sensitivity : Sensitivity) (contentSafety : ContentSafety) : Bool :=
  !(sensitivity == .secret) && contentSafety == .secretFree

end AIContextFormal

import AIContextFormal.Authority

namespace AIContextFormal

inductive MutationField where
  | confidence
  | epistemicVerification
  | lifecycle
  | content
  | sensitivity
  | recordClass
  | tags
  | provenance
  deriving Repr, DecidableEq, BEq

def mutationAllowed : MutationField → Bool
  | .confidence => true
  | .epistemicVerification => true
  | .lifecycle => true
  | .content => false
  | .sensitivity => false
  | .recordClass => false
  | .tags => false
  | .provenance => false

structure CurationDecision where
  actor : Actor
  decision : ReviewDecision
  conflictClear : Bool
  deriving Repr, DecidableEq

def authorizesApplication (d : CurationDecision) : Bool :=
  canApply d.actor d.decision && d.conflictClear

structure Supersession where
  oldId : String
  newId : String
  deriving Repr, DecidableEq

def validSupersession (s : Supersession) : Bool :=
  !(s.oldId == s.newId)

def tombstoneDeletesHistory : Bool := false

end AIContextFormal

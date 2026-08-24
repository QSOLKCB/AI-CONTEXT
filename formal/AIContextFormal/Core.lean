namespace AIContextFormal

def frozenTag : String := "v1.0.0"
def frozenCommit : String := "53d7d69dfacecf6f8605f5b6a51b2c68ee66572a"
def frozenTree : String := "2c0592cbd074d7596e70681cc5ed869d6b9b00e4"

inductive TrustZone where
  | rawVault
  | staging
  | canonicalMemory
  | routedBundle
  | externalConsumer
  deriving Repr, DecidableEq, BEq

inductive RecordClass where
  | fact
  | preference
  | projectState
  | decision
  | claim
  | hypothesis
  | instruction
  | relationship
  | publication
  | event
  | environment
  | provenancePolicy
  deriving Repr, DecidableEq, BEq

inductive Sensitivity where
  | public
  | private
  | restricted
  | secret
  deriving Repr, DecidableEq, BEq

inductive EpistemicState where
  | observed
  | retrieved
  | parsed
  | inferred
  | remembered
  | userAsserted
  | verified
  | unknown
  deriving Repr, DecidableEq, BEq

inductive Lifecycle where
  | active
  | expired
  | superseded
  | tombstoned
  deriving Repr, DecidableEq, BEq

inductive AuthorityLevel where
  | none
  | evidence
  | review
  | canonicalMemory
  | disclosure
  deriving Repr, DecidableEq, BEq

inductive DisclosureTarget where
  | localModel
  | externalProvider
  | agent
  | tool
  deriving Repr, DecidableEq, BEq

inductive Actor where
  | human
  | policy
  | localLLM
  | provider
  | toolAdapter
  | tui
  | candidateGenerator
  deriving Repr, DecidableEq, BEq

inductive ReviewDecision where
  | approve
  | reject
  | revise
  deriving Repr, DecidableEq, BEq

inductive FactualAuthority where
  | none
  | evidence
  | canonical
  deriving Repr, DecidableEq, BEq

structure MemoryState where
  recordClass : RecordClass
  sensitivity : Sensitivity
  epistemicState : EpistemicState
  lifecycle : Lifecycle
  deriving Repr, DecidableEq

end AIContextFormal

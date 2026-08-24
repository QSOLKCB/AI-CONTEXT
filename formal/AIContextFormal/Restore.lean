import AIContextFormal.Core

namespace AIContextFormal

inductive RestoreClaim where
  | contextContinuity
  | modelIdentity
  deriving Repr, DecidableEq, BEq

def ValidRestoreClaim : RestoreClaim → Prop
  | .contextContinuity => True
  | .modelIdentity => False

structure RestoreManifest where
  providerMemoryDependency : Bool
  deriving Repr, DecidableEq

def ValidRestoreManifest (manifest : RestoreManifest) : Prop :=
  manifest.providerMemoryDependency = false

inductive EnrichmentClass where
  | styleCulture
  deriving Repr, DecidableEq, BEq

def enrichmentFactualAuthority : EnrichmentClass → FactualAuthority
  | .styleCulture => .none

end AIContextFormal

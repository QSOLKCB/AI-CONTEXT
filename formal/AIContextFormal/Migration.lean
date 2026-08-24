import AIContextFormal.Core

namespace AIContextFormal

structure Version where
  major : Nat
  minor : Nat
  patch : Nat
  deriving Repr, DecidableEq

def referenceVersion : Version :=
  { major := 0, minor := 1, patch := 0 }

def majorCompatible (supported incoming : Version) : Bool :=
  supported.major == incoming.major

inductive MigrationMetadataClass where
  | authoritative
  | extension
  deriving Repr, DecidableEq, BEq

def migrationMetadataAuthority : MigrationMetadataClass → AuthorityLevel
  | .authoritative => .canonicalMemory
  | .extension => .none

end AIContextFormal

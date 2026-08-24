import AIContextFormal.Core

namespace AIContextFormal

structure IndexState where
  fingerprintMatches : Bool
  deriving Repr, DecidableEq

def usableIndex (state : IndexState) : Bool :=
  state.fingerprintMatches

def indexAuthority (_state : IndexState) : AuthorityLevel :=
  .none

def indexMembershipDiscloses (_state : IndexState) : Bool :=
  false

end AIContextFormal

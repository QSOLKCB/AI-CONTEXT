import AIContextFormal.Core

namespace AIContextFormal

def storageEpistemicAuthority (_encrypted : Bool) : AuthorityLevel :=
  .none

inductive ErasureScope where
  | primaryCiphertext
  | allBackups
  | allKeyCopies
  deriving Repr, DecidableEq, BEq

def deletionReceiptClaims : ErasureScope → Bool
  | .primaryCiphertext => true
  | .allBackups => false
  | .allKeyCopies => false

end AIContextFormal

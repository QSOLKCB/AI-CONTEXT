import AIContextFormal.Core

namespace AIContextFormal

inductive UXAction where
  | status
  | provenance
  | conflictPreview
  | bundlePreview
  | reviewPrompt
  | applyPrompt
  | backup
  | restore
  deriving Repr, DecidableEq, BEq

def uxProtocolAuthority (_action : UXAction) : AuthorityLevel :=
  .none

def previewDiscloses : Bool := false

end AIContextFormal

import AIContextFormal.Core

namespace AIContextFormal

inductive TransportEvidence where
  | signedReceipt
  | capabilityManifest
  | toolAdvertisement
  deriving Repr, DecidableEq, BEq

def transportEpistemicAuthority (_evidence : TransportEvidence) : AuthorityLevel :=
  .none

def transportDisclosureAuthority (_evidence : TransportEvidence) : AuthorityLevel :=
  .none

end AIContextFormal

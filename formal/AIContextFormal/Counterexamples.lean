import AIContextFormal.Curation
import AIContextFormal.Disclosure
import AIContextFormal.Restore
import AIContextFormal.Indexes
import AIContextFormal.Interop
import AIContextFormal.UX
import AIContextFormal.Migration

namespace AIContextFormal

def invalidPromotionActor : Actor := .localLLM

def invalidDisclosure : DisclosureState := {
  relevant := True
  approved := False
  active := True
  sensitivityAllowed := True
  hardExcluded := False
  dependency := False
}

def invalidDependencyDisclosure : DisclosureState := {
  relevant := False
  approved := False
  active := True
  sensitivityAllowed := True
  hardExcluded := False
  dependency := True
}

def unknownMajorVersion : Version := {
  major := 1
  minor := 0
  patch := 0
}

def invalidRestoreClaim : RestoreClaim := .modelIdentity

def staleIndex : IndexState := { fingerprintMatches := false }

def signatureEvidence : TransportEvidence := .signedReceipt

def toolEvidence : TransportEvidence := .toolAdvertisement

def invalidUXAction : UXAction := .applyPrompt

end AIContextFormal

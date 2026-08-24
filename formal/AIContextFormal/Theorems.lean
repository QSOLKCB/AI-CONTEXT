import AIContextFormal.Authority
import AIContextFormal.Curation
import AIContextFormal.Disclosure
import AIContextFormal.Restore
import AIContextFormal.Storage
import AIContextFormal.Indexes
import AIContextFormal.Interop
import AIContextFormal.UX
import AIContextFormal.Migration
import AIContextFormal.Counterexamples

namespace AIContextFormal

theorem frozenTagBound : frozenTag = "v1.0.0" := rfl

theorem frozenCommitBound :
    frozenCommit = "53d7d69dfacecf6f8605f5b6a51b2c68ee66572a" := rfl

theorem frozenTreeBound :
    frozenTree = "2c0592cbd074d7596e70681cc5ed869d6b9b00e4" := rfl

/-- No importer has a direct raw-source-to-canonical-memory transition. -/
theorem sourceMaterialNotCanonicalMemory (importer : SourceImporter) :
    ¬ ImportTransition importer .rawVault .canonicalMemory := by
  intro transition
  cases transition

theorem stagingNotCanonicalMemory :
    TrustZone.staging ≠ TrustZone.canonicalMemory := by decide

theorem candidateCannotSelfApply :
    canApply .candidateGenerator .approve = false := rfl

theorem localLLMCannotReview :
    isReviewAuthority .localLLM = false := rfl

theorem privateToPublicDowngradeRejected :
    canReclassify .private .public = false := by decide

/-- Secret-shaped material is ineligible regardless of the sensitivity label. -/
theorem secretMemoryExcluded (sensitivity : Sensitivity) :
    canonicalEligible sensitivity .secretShaped = false := by
  cases sensitivity <;> rfl

theorem approvalWithoutConflictClearBlocked :
    authorizesApplication {
      actor := .human
      decision := .approve
      conflictClear := false
    } = false := rfl

theorem contentMutationForbidden :
    mutationAllowed .content = false := rfl

theorem sensitivityMutationForbidden :
    mutationAllowed .sensitivity = false := rfl

theorem selfSupersessionRejected :
    validSupersession { oldId := "memory.same", newId := "memory.same" } = false := by decide

theorem tombstonePreservesHistory :
    tombstoneDeletesHistory = false := rfl

theorem selectedImpliesPermitted {state : DisclosureState} :
    Selected state → Permitted state := fun h => h.2

theorem dependencySelectedImpliesPermitted {state : DisclosureState} :
    DependencySelected state → Permitted state := fun h => h.2

theorem relevanceDoesNotGrantPermission :
    ¬ Selected invalidDisclosure := by
  intro h
  exact h.2.1

theorem dependencyDoesNotBypassPermission :
    ¬ DependencySelected invalidDependencyDisclosure := by
  intro h
  exact h.2.1

theorem restoreContinuityNotModelIdentity :
    ¬ ValidRestoreClaim .modelIdentity := fun h => h

theorem restoreRequiresNoProviderMemory :
    ValidRestoreManifest { providerMemoryDependency := false } := rfl

theorem styleCultureHasNoFactualAuthority :
    enrichmentFactualAuthority .styleCulture = .none := rfl

theorem encryptionAtRestHasNoEpistemicAuthority (encrypted : Bool) :
    storageEpistemicAuthority encrypted = .none := rfl

theorem primaryDeletionDoesNotClaimBackupDestruction :
    deletionReceiptClaims .allBackups = false := rfl

theorem primaryDeletionDoesNotClaimKeyDestruction :
    deletionReceiptClaims .allKeyCopies = false := rfl

theorem staleIndexUnusable :
    usableIndex staleIndex = false := rfl

theorem indexHitHasNoMemoryAuthority :
    indexAuthority { fingerprintMatches := true } = .none := rfl

theorem indexMembershipDoesNotDisclose :
    indexMembershipDiscloses { fingerprintMatches := true } = false := rfl

theorem signatureHasNoDisclosureAuthority :
    transportDisclosureAuthority .signedReceipt = .none := rfl

theorem signatureHasNoEpistemicAuthority :
    transportEpistemicAuthority .signedReceipt = .none := rfl

theorem capabilityClaimHasNoPermissionAuthority :
    transportDisclosureAuthority .capabilityManifest = .none := rfl

theorem toolAdvertisementHasNoMemoryAuthority :
    transportEpistemicAuthority .toolAdvertisement = .none := rfl

theorem uxActionHasNoProtocolAuthority (action : UXAction) :
    uxProtocolAuthority action = .none := rfl

theorem previewIsNotDisclosure :
    previewDiscloses = false := rfl

theorem unknownMajorRejected :
    majorCompatible referenceVersion unknownMajorVersion = false := rfl

theorem migrationExtensionsNonAuthoritative :
    migrationMetadataAuthority .extension = .none := rfl

-- Finite invalid-state counterexamples required by the archival proof plan.
example : canApply invalidPromotionActor .approve = false := rfl
example : ¬ Selected invalidDisclosure := relevanceDoesNotGrantPermission
example : majorCompatible referenceVersion unknownMajorVersion = false := unknownMajorRejected
example : ¬ ValidRestoreClaim invalidRestoreClaim := restoreContinuityNotModelIdentity
example : usableIndex staleIndex = false := staleIndexUnusable
example : transportDisclosureAuthority signatureEvidence = .none := signatureHasNoDisclosureAuthority
example : transportEpistemicAuthority toolEvidence = .none := toolAdvertisementHasNoMemoryAuthority
example : uxProtocolAuthority invalidUXAction = .none := uxActionHasNoProtocolAuthority invalidUXAction
example : canonicalEligible .private .secretShaped = false := secretMemoryExcluded .private
example (importer : SourceImporter) :
    ¬ ImportTransition importer .rawVault .canonicalMemory :=
  sourceMaterialNotCanonicalMemory importer

end AIContextFormal

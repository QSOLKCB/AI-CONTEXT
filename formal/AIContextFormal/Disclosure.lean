import AIContextFormal.Core

namespace AIContextFormal

structure DisclosureState where
  relevant : Prop
  approved : Prop
  active : Prop
  sensitivityAllowed : Prop
  hardExcluded : Prop
  dependency : Prop

def Permitted (s : DisclosureState) : Prop :=
  s.approved ∧ s.active ∧ s.sensitivityAllowed ∧ ¬ s.hardExcluded

def Selected (s : DisclosureState) : Prop :=
  s.relevant ∧ Permitted s

def DependencySelected (s : DisclosureState) : Prop :=
  s.dependency ∧ Permitted s

end AIContextFormal

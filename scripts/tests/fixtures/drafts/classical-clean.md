# Consent under the GDPR: lawfulness of a marketing flow

Date: 2026-01-02.

Jurisdictions: EU.

Question: whether the company may rely on consent for the marketing flow.

Template: classical-memo.

The company is considering a marketing flow for EU users. This memo evaluates the lawful basis. Vendor selection is out of scope.

## 1. Executive summary

- Consent is available as a lawful basis for the marketing flow. Risk: medium.
- The withdrawal control is the operative gap and must ship before launch. Risk: high.

## 2. Facts, assumptions and limitations

The company collects contact data from users located in the EU. The flow has no withdrawal control today. The analysis assumes that the users are consumers.

## 3. Lawful basis for the marketing flow

### 3.1. Consent as the basis of the flow

Consent is a lawful basis for the processing at issue [[src:gdpr-art-6 Art. 6(1)(a)]].

> [[q:q-gdpr-art-6-1]] Consent must be freely given, specific, informed and unambiguous.

The provision makes consent one of six bases and sets the quality bar for it. A contrary reading treats a pre-ticked box as consent, and that reading fails because the provision requires an unambiguous indication. For this flow the bar is met once the user acts affirmatively.

Risk: medium. The basis holds only while the affirmative action stays in the flow. Product must keep the opt-in unticked before launch.

### 3.2. Withdrawal of consent

Withdrawal must be as easy as giving consent [[src:gdpr-art-7 Art. 7(3)]].

The flow has no withdrawal control today, so the requirement is unmet. A contrary reading is that an email to support suffices, and it fails because the standard is symmetry with the opt-in. The gap is operational rather than legal.

Risk: high. A regulator would treat the asymmetry as a defect of the consent itself. Legal must ship a one-click withdrawal control before launch.

## 4. Conclusion and recommendations

- Keep the opt-in unticked at launch, owned by Product, before the flow ships.
- Ship a one-click withdrawal control, owned by Legal, before the flow ships.

<!-- sources: generated -->

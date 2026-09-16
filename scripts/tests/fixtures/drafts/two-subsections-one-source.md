# Consent under the GDPR: one source, two subsections

Date: 2026-01-02.

Jurisdictions: EU.

Question: whether the company may rely on consent for two related flows.

Template: classical-memo.

The company runs two flows on the same provision. This memo evaluates both. Vendor selection is out of scope.

## 1. Executive summary

- Consent is available as a lawful basis for the marketing flow. Risk: medium.
- The analytics flow rests on the same provision and the same passage. Risk: medium.

## 2. Facts, assumptions and limitations

The company collects contact data from users located in the EU. Both flows rely on the same consent screen. The analysis assumes that the users are consumers.

## 3. Lawful basis for both flows

### 3.1. Consent for the marketing flow

Consent is a lawful basis for the marketing flow [[src:gdpr-art-6 Art. 6(1)(a)]].

> [[q:q-gdpr-art-6-1]] Consent must be freely given, specific, informed and unambiguous.

The provision sets the quality bar for consent in both flows. A contrary reading treats a pre-ticked box as consent, and it fails because the provision requires an unambiguous indication. The bar is met once the user acts affirmatively.

Risk: medium. The basis holds only while the affirmative action stays in the flow. Product must keep the opt-in unticked before launch.

### 3.2. Consent for the analytics flow

The analytics flow rests on the same provision, whose only quotable passage is already quoted above [[src:gdpr-art-6 Art. 6(1)(a)]].

The passage requires the same unambiguous indication for analytics as for marketing. A contrary reading treats analytics as a legitimate interest, and it fails because the company already asked for consent. The two flows therefore stand or fall together.

Risk: medium. Splitting the screens would let one flow fail without the other. Product must keep the two consents separate before launch.

## 4. Conclusion and recommendations

- Keep the opt-in unticked at launch, owned by Product, before the flow ships.
- Separate the two consent screens, owned by Product, before the analytics flow ships.

<!-- sources: generated -->

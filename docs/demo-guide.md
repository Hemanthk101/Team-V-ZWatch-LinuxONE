# Team V - ZWatch demo and judging guide

## What to say

ZWatch helps small teams running campus and community digital services interpret sustained resource changes. Six metrics are collected from our LinuxONE VM. A local Isolation Forest and critical resource guardrails produce candidate signals. Three consecutive candidates open an incident with observed deviations and investigation guidance. Three clear samples resolve it. An operator reviews and acknowledges the incident; the system does not execute destructive actions.

## A clear demo sequence

1. Open the private dashboard using the supplied access instructions. On Live monitor, show the s390x runtime, real metrics, and the ready live baseline.
2. Open Failure replay and choose Cascading service degradation. State clearly that this replay uses synthetic metrics and a separate model.
3. Click Run failure replay. The first coordinated episode stays below critical guardrails. The model crosses its threshold and the incident policy confirms a warning.
4. Show the baseline deviations and investigation runbook. Explain that these are observed signals, not a proven cause. Acknowledge an incident and show recovery.
5. Open Evidence & design. Show 10/10 synthetic events detected versus 9/10 using the fixed guardrails alone, the healthy control, the measured LinuxONE inference timing, and the limitations.

## Questions to prepare for

**Is this a new anomaly detection algorithm?**
No. Isolation Forest is an established open-source algorithm. Our contribution is the working local LinuxONE workflow: separate real and replay models, calibration, sustained incident handling, interpretable deviations, runbooks, persistence, and reproducible evidence.

**What data did you use?**
The live monitor uses actual VM counters from psutil. Its short prototype baseline uses 80 training observations plus 40 independent calibration observations. Replay evaluation uses generated normal training data, independent calibration data, and separately seeded synthetic stress scenarios. We do not claim that synthetic evaluation proves field accuracy.

**Why use LinuxONE?**
The collector, model, API, and database run on the provided LinuxONE s390x VM. Training and CPU inference remain local, without an external AI service. We verified the architecture and recorded inference timing on the actual platform. We do not claim Telum acceleration or protected execution.

**What does the anomaly score mean?**
It is the negative Isolation Forest score_samples output, not a probability of failure. The threshold is the 99.5th percentile of a separate normal calibration set. A positive robust baseline deviation is also required for the model-driven candidate, or a critical guardrail can create one.

**What does the comparison prove?**
In this small controlled evaluation, both approaches detect severe individual resource breaches. The first compound episode changes several metrics together below the critical guardrails, so the model adds one detected episode. This is a reproducible demonstration, not a universal benchmark against every monitoring product.

**Why do point precision and recall show about 93.8%?**
The reported point labels include the incident policy. Confirmation deliberately waits for the third abnormal observation, and recovery deliberately waits for the third clear observation. This introduces two delayed samples at each edge. The full confusion counts are published, along with event detection and delay measured in sample steps.

**How are false alerts limited?**
Calibration, minimum robust deviation, and three-sample confirmation suppress brief spikes. The short live baseline is a demo limitation; deployment needs a longer vetted baseline covering legitimate workload variations, drift review, and field measurement of alert burden.

**What engineering is verified?**
Twelve automated checks passed, including authentication, foreign-origin mutation rejection, input validation, spike suppression, healthy control, recovery, and model isolation. A separate restart check preserved the 120-observation live baseline, 180 replay observations, two incidents, and an acknowledgment. Reports are in the repository.

**Is it ready for production?**
It is a working production-oriented prototype with private SSH access, an authenticated API, reduced container privileges, bounded sample retention, and an audit trail. Multi-user production use needs representative longer data, TLS plus organizational SSO/RBAC, signed agents, drift management, availability testing, and validated operating procedures.

**Was AI used?**
Yes. AI assisted implementation and documentation. Explain this directly and follow the judges' disclosure requirements. Do not invent individual team contributions or imply that provided IBM lab code is the original project.

## Submission completion

- Project title: **Team V_IBM Z Datathon 2026 Project - ZWatch**
- Select the matching **Real-Time AI for Critical Decisions / System Anomaly Detection** challenge shown by the form.
- Use the supplied field answers, public GitHub link, and PDF deck.
- Upload the supplied caption-led MP4 to YouTube or Vimeo. Public or unlisted access must work for judges. Paste that actual URL in Video Submission Link.
- Final deadline shown in your form: **18 October 2026, 10:00 PM IST**.

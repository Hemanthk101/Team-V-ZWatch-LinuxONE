# Team V - ZWatch submission

Project title: **Team V_IBM Z Datathon 2026 Project - ZWatch**

Deadline supplied by participant: **18 October 2026, 10:00 PM IST**.

## Challenges

Select the matching Real-Time AI for Critical Decisions / System Anomaly Detection option displayed by Hack2Skill.

## What is the Problem you are trying to solve?

Small teams running campus, community, or small business digital services must interpret CPU, memory, network, storage, and I/O signals before resource pressure disrupts service. Isolated fixed thresholds can miss coordinated changes across several metrics, while short spikes can generate distracting alerts. Operators need a local monitoring system that identifies sustained unusual behavior, shows the observed deviations, and preserves an incident trail with practical investigation guidance. ZWatch addresses system anomaly detection for these resource-constrained operations teams.

## How are you planning to solve the problem? (Brief Overview)

ZWatch is a working Python/FastAPI dashboard running on LinuxONE. psutil collects six resource metrics approximately every second. A 128-tree Isolation Forest learns a baseline; an independent calibration set sets its anomaly threshold. Critical resource guardrails add coverage for severe individual signals. Three consecutive abnormal samples open an incident; three clean samples resolve it. Robust baseline deviations and review runbooks explain each incident. SQLite WAL stores samples, incidents and audit records; CSV and JSON exports support review. Live training uses real VM observations. A separate synthetic replay model demonstrates five stress patterns and a healthy control without stressing the VM or entering live training data.

## How does your solution leverage IBM Z / LinuxONE technologies?

The collector, AI inference, FastAPI service, SQLite store, and dashboard are deployed on the provided IBM LinuxONE Community Cloud VM. Runtime evidence verifies s390x, Python 3.12.15, three logical CPUs and 7.82 GiB VM memory. The app runs in a constrained container with a read-only source mount, reduced capabilities and restart policy. Both model training and inference execute locally on LinuxONE CPU, keeping raw telemetry off external AI APIs. Published evaluation includes measured inference timing from this VM. This prototype uses LinuxONE compute and Linux/container support; it does not claim Telum acceleration, protected execution or high availability.

## What value does your solution deliver, and who will benefit from it?

Target users are small IT and operations teams supporting campus, community and small business digital services. ZWatch turns resource counters into sustained, explainable incidents with investigation guidance, acknowledgment and an audit trail. Local inference avoids an external AI service dependency. In held-out synthetic evaluation, it detected all 10 injected stress events, including a coordinated episode that static guardrails missed; the healthy control generated no confirmed incident. Expected practical value is faster triage and more informed resource decisions. These controlled results are not a production accuracy claim; outage reduction and ROI require field validation.

## Github Link of your Project / Link of your Zip File

https://github.com/Hemanthk101/Team-V-ZWatch-LinuxONE

## Video Submission Link

Upload the supplied Team-V-ZWatch-Demo.mp4 to YouTube or Vimeo and paste its public/unlisted accessible URL. Do not use a placeholder URL.

## Share the link to your pitch deck in PDF Format

https://github.com/Hemanthk101/Team-V-ZWatch-LinuxONE/blob/main/docs/Team-V-ZWatch-Pitch.pdf

## Link to all open-source resources used

https://scikit-learn.org/ ; https://numpy.org/ ; https://github.com/giampaolo/psutil ; https://github.com/fastapi/fastapi ; https://github.com/encode/uvicorn ; https://www.python.org/ ; https://www.sqlite.org/ ; https://github.com/linuxone-community-cloud/jupyter-lab-ml ; https://github.com/heygen-com/hyperframes ; https://www.reportlab.com/opensource/ ; https://ffmpeg.org/ ; https://python-pillow.org/ ; https://gsap.com/ ; https://github.com/psf/black . Synthetic replay data is generated in this project; live data comes from the LinuxONE VM. AI assisted implementation and documentation.

## Any other document

https://github.com/Hemanthk101/Team-V-ZWatch-LinuxONE/blob/main/docs/architecture.svg


# Parallel progress preview (synthetic data)

These are rendered examples, not a production run or an installable live card. OneDrive, GDrive and Mega are labels for three configured destinations.

## Three uploads running

## rclone-multithreaded-upload — Job Monitor

| 🟢 Running | ✅ Finished | 🔴 Failed | ⚪ Other / unknown |
|:---:|:---:|:---:|:---:|
| **1** | **0** | **0** | **0** |

**1 job(s) discovered**

---

### 🟢 Frigate CCTV Upload — RUNNING

**Phase:** Upload: 3

**Current:** OneDrive: Upload files (copy); GDrive: Upload files (copy); Mega: Upload files (copy)

**Overall run progress**

`████░░░░░░` **40.0%**

**Steps:** 14 / 35 settled

**Destinations:** 3 · **Active destinations:** 3 · **Running steps:** 3 · **Failed steps:** 0 · **Skipped steps:** 4

**Destinations — live overview**

| Destination | Activity / current step | Settled steps | Progress | State |
|:---|:---|:---:|:---:|:---|
| OneDrive | Upload: Upload files (copy) | 2 / 7 | 28.57% | RUNNING |
| GDrive | Upload: Upload files (copy) | 5 / 12 | 41.67% | RUNNING |
| Mega | Upload: Upload files (copy) | 7 / 16 | 43.75% | RUNNING |

#### OneDrive — RUNNING

/srv/frigate → onedrive:Frigate

**Progress:** 2 / 7 steps settled (28.57%) · 1 succeeded · 0 failed · 1 skipped

**Active:** Upload files (copy)

| Step | Phase | State | Detail |
|:---|:---|:---|:---|
| Pre-upload remote snapshot | reservation | ✅ SUCCESS |  |
| Execute pre-upload delete plan | reservation | ⏭ SKIPPED | No script-managed deletion/trash activity in this synthetic example |
| Upload files (copy) | upload | 🟢 RUNNING |  |
| Post-upload remote snapshot | post_cleanup | ⏳ PENDING |  |
| Execute post-upload delete plan | post_cleanup | ⏳ PENDING |  |
| Final remote snapshot | final_quota | ⏳ PENDING |  |
| Verify all cleanup and remote limits | final_quota | ⏳ PENDING |  |

#### GDrive — RUNNING

/srv/frigate → gdrive:Frigate

**Progress:** 5 / 12 steps settled (41.67%) · 4 succeeded · 0 failed · 1 skipped

**Active:** Upload files (copy)

**Individual cleanup rules — planning progress**

| Cleanup path | Settled | Pre-upload | Post-upload |
|:---|:---|:---|:---|
| gdrive:Frigate/ | 1 / 2 | SUCCESS | PENDING |

| Step | Phase | State | Detail |
|:---|:---|:---|:---|
| Pre-upload remote snapshot | reservation | ✅ SUCCESS |  |
| Acquire filtered local file snapshot | reservation | ✅ SUCCESS |  |
| Plan pre-upload cleanup: gdrive:Frigate/ | reservation | ✅ SUCCESS |  |
| Reserve space for selected upload files | reservation | ✅ SUCCESS |  |
| Execute pre-upload delete plan | reservation | ⏭ SKIPPED | No script-managed deletion/trash activity in this synthetic example |
| Upload files (copy) | upload | 🟢 RUNNING |  |
| Post-upload remote snapshot | post_cleanup | ⏳ PENDING |  |
| Plan post-upload cleanup: gdrive:Frigate/ | post_cleanup | ⏳ PENDING |  |
| Plan post-upload total-size cleanup | post_cleanup | ⏳ PENDING |  |
| Execute post-upload delete plan | post_cleanup | ⏳ PENDING |  |
| Final remote snapshot | final_quota | ⏳ PENDING |  |
| Verify all cleanup and remote limits | final_quota | ⏳ PENDING |  |

#### Mega — RUNNING

/srv/frigate → mega:Frigate

**Progress:** 7 / 16 steps settled (43.75%) · 5 succeeded · 0 failed · 2 skipped

**Active:** Upload files (copy)

**Individual cleanup rules — planning progress**

| Cleanup path | Settled | Pre-upload | Post-upload |
|:---|:---|:---|:---|
| mega:Frigate/ | 1 / 2 | SUCCESS | PENDING |
| mega:Frigate/Camera | 1 / 2 | SUCCESS | PENDING |

| Step | Phase | State | Detail |
|:---|:---|:---|:---|
| Pre-upload remote snapshot | reservation | ✅ SUCCESS |  |
| Acquire filtered local file snapshot | reservation | ✅ SUCCESS |  |
| Plan pre-upload cleanup: mega:Frigate/ | reservation | ✅ SUCCESS |  |
| Plan pre-upload cleanup: mega:Frigate/Camera | reservation | ✅ SUCCESS |  |
| Reserve space for selected upload files | reservation | ✅ SUCCESS |  |
| Execute pre-upload delete plan | reservation | ⏭ SKIPPED | No script-managed deletion/trash activity in this synthetic example |
| Pre-upload trash cleanup | reservation | ⏭ SKIPPED | No script-managed deletion/trash activity in this synthetic example |
| Upload files (copy) | upload | 🟢 RUNNING |  |
| Post-upload remote snapshot | post_cleanup | ⏳ PENDING |  |
| Plan post-upload cleanup: mega:Frigate/ | post_cleanup | ⏳ PENDING |  |
| Plan post-upload cleanup: mega:Frigate/Camera | post_cleanup | ⏳ PENDING |  |
| Plan post-upload total-size cleanup | post_cleanup | ⏳ PENDING |  |
| Execute post-upload delete plan | post_cleanup | ⏳ PENDING |  |
| Post-upload trash cleanup | post_cleanup | ⏳ PENDING |  |
| Final remote snapshot | final_quota | ⏳ PENDING |  |
| Verify all cleanup and remote limits | final_quota | ⏳ PENDING |  |

**Transferred:** Unknown

**Average speed:** Unknown

**Runtime:** 00:00:00

_Running is determined by the live heartbeat. Expired live sensors use the retained result unless a different unfinished run is known. Unknown progress stays unknown. Steps are settled workflow observations, including failures and skips; destination progress is tracked independently. Generic jobs without detailed progress use their configured item unit. Average speed is a run average._

## Mixed active operations in a separate example

## rclone-multithreaded-upload — Job Monitor

| 🟢 Running | ✅ Finished | 🔴 Failed | ⚪ Other / unknown |
|:---:|:---:|:---:|:---:|
| **1** | **0** | **0** | **0** |

**1 job(s) discovered**

---

### 🟢 Frigate CCTV Upload — RUNNING

**Phase:** Upload: 1; Cleanup: 1; Trash cleanup: 1

**Current:** OneDrive: Upload files (copy); GDrive: Execute pre-upload delete plan; Mega: Pre-upload trash cleanup

**Overall run progress**

`░░░░░░░░░░` **0%**

**Steps:** 0 / 35 settled

**Destinations:** 3 · **Active destinations:** 3 · **Running steps:** 3 · **Failed steps:** 0 · **Skipped steps:** 0

**Destinations — live overview**

| Destination | Activity / current step | Settled steps | Progress | State |
|:---|:---|:---:|:---:|:---|
| OneDrive | Upload: Upload files (copy) | 0 / 7 | 0.0% | RUNNING |
| GDrive | Cleanup: Execute pre-upload delete plan | 0 / 12 | 0.0% | RUNNING |
| Mega | Trash cleanup: Pre-upload trash cleanup | 0 / 16 | 0.0% | RUNNING |

#### OneDrive — RUNNING

/srv/frigate → onedrive:Frigate

**Progress:** 0 / 7 steps settled (0.0%) · 0 succeeded · 0 failed · 0 skipped

**Active:** Upload files (copy)

| Step | Phase | State | Detail |
|:---|:---|:---|:---|
| Pre-upload remote snapshot | reservation | ⏳ PENDING |  |
| Execute pre-upload delete plan | reservation | ⏳ PENDING |  |
| Upload files (copy) | upload | 🟢 RUNNING |  |
| Post-upload remote snapshot | post_cleanup | ⏳ PENDING |  |
| Execute post-upload delete plan | post_cleanup | ⏳ PENDING |  |
| Final remote snapshot | final_quota | ⏳ PENDING |  |
| Verify all cleanup and remote limits | final_quota | ⏳ PENDING |  |

#### GDrive — RUNNING

/srv/frigate → gdrive:Frigate

**Progress:** 0 / 12 steps settled (0.0%) · 0 succeeded · 0 failed · 0 skipped

**Active:** Execute pre-upload delete plan

**Individual cleanup rules — planning progress**

| Cleanup path | Settled | Pre-upload | Post-upload |
|:---|:---|:---|:---|
| gdrive:Frigate/ | 0 / 2 | PENDING | PENDING |

| Step | Phase | State | Detail |
|:---|:---|:---|:---|
| Pre-upload remote snapshot | reservation | ⏳ PENDING |  |
| Acquire filtered local file snapshot | reservation | ⏳ PENDING |  |
| Plan pre-upload cleanup: gdrive:Frigate/ | reservation | ⏳ PENDING |  |
| Reserve space for selected upload files | reservation | ⏳ PENDING |  |
| Execute pre-upload delete plan | reservation | 🟢 RUNNING |  |
| Upload files (copy) | upload | ⏳ PENDING |  |
| Post-upload remote snapshot | post_cleanup | ⏳ PENDING |  |
| Plan post-upload cleanup: gdrive:Frigate/ | post_cleanup | ⏳ PENDING |  |
| Plan post-upload total-size cleanup | post_cleanup | ⏳ PENDING |  |
| Execute post-upload delete plan | post_cleanup | ⏳ PENDING |  |
| Final remote snapshot | final_quota | ⏳ PENDING |  |
| Verify all cleanup and remote limits | final_quota | ⏳ PENDING |  |

#### Mega — RUNNING

/srv/frigate → mega:Frigate

**Progress:** 0 / 16 steps settled (0.0%) · 0 succeeded · 0 failed · 0 skipped

**Active:** Pre-upload trash cleanup

**Individual cleanup rules — planning progress**

| Cleanup path | Settled | Pre-upload | Post-upload |
|:---|:---|:---|:---|
| mega:Frigate/ | 0 / 2 | PENDING | PENDING |
| mega:Frigate/Camera | 0 / 2 | PENDING | PENDING |

| Step | Phase | State | Detail |
|:---|:---|:---|:---|
| Pre-upload remote snapshot | reservation | ⏳ PENDING |  |
| Acquire filtered local file snapshot | reservation | ⏳ PENDING |  |
| Plan pre-upload cleanup: mega:Frigate/ | reservation | ⏳ PENDING |  |
| Plan pre-upload cleanup: mega:Frigate/Camera | reservation | ⏳ PENDING |  |
| Reserve space for selected upload files | reservation | ⏳ PENDING |  |
| Execute pre-upload delete plan | reservation | ⏳ PENDING |  |
| Pre-upload trash cleanup | reservation | 🟢 RUNNING |  |
| Upload files (copy) | upload | ⏳ PENDING |  |
| Post-upload remote snapshot | post_cleanup | ⏳ PENDING |  |
| Plan post-upload cleanup: mega:Frigate/ | post_cleanup | ⏳ PENDING |  |
| Plan post-upload cleanup: mega:Frigate/Camera | post_cleanup | ⏳ PENDING |  |
| Plan post-upload total-size cleanup | post_cleanup | ⏳ PENDING |  |
| Execute post-upload delete plan | post_cleanup | ⏳ PENDING |  |
| Post-upload trash cleanup | post_cleanup | ⏳ PENDING |  |
| Final remote snapshot | final_quota | ⏳ PENDING |  |
| Verify all cleanup and remote limits | final_quota | ⏳ PENDING |  |

**Transferred:** Unknown

**Average speed:** Unknown

**Runtime:** 00:00:00

_Running is determined by the live heartbeat. Expired live sensors use the retained result unless a different unfinished run is known. Unknown progress stays unknown. Steps are settled workflow observations, including failures and skips; destination progress is tracked independently. Generic jobs without detailed progress use their configured item unit. Average speed is a run average._

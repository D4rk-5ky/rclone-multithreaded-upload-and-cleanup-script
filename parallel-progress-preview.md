# Parallel progress preview (synthetic data)

This is a rendered example, not a production run or an installable live card.

## rclone-multithreaded-upload — Job Monitor

| 🟢 Running | ✅ Finished | 🔴 Failed | ⚪ Other / unknown |
|:---:|:---:|:---:|:---:|
| **1** | **0** | **0** | **0** |

**1 job(s) discovered**

---

### 🟢 Frigate CCTV Upload — RUNNING

**Phase:** reservation_and_upload

**Current:** Archive A: Upload files (copy); Archive B: Upload files (copy); Archive C: Upload files (copy)

`████░░░░░░` **40.0%**

**Steps:** 14 / 35 settled

**Destinations:** 3 · **Active destinations:** 3 · **Running steps:** 3 · **Failed steps:** 0 · **Skipped steps:** 4

#### Archive A — RUNNING

/srv/frigate → cloud:a

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

#### Archive B — RUNNING

/srv/frigate → cloud:b

**Progress:** 5 / 12 steps settled (41.67%) · 4 succeeded · 0 failed · 1 skipped

**Active:** Upload files (copy)

**Individual cleanup rules — planning progress**

| Cleanup path | Settled | Pre-upload | Post-upload |
|:---|:---|:---|:---|
| cloud:b/ | 1 / 2 | SUCCESS | PENDING |

| Step | Phase | State | Detail |
|:---|:---|:---|:---|
| Pre-upload remote snapshot | reservation | ✅ SUCCESS |  |
| Acquire filtered local file snapshot | reservation | ✅ SUCCESS |  |
| Plan pre-upload cleanup: cloud:b/ | reservation | ✅ SUCCESS |  |
| Reserve space for selected upload files | reservation | ✅ SUCCESS |  |
| Execute pre-upload delete plan | reservation | ⏭ SKIPPED | No script-managed deletion/trash activity in this synthetic example |
| Upload files (copy) | upload | 🟢 RUNNING |  |
| Post-upload remote snapshot | post_cleanup | ⏳ PENDING |  |
| Plan post-upload cleanup: cloud:b/ | post_cleanup | ⏳ PENDING |  |
| Plan post-upload total-size cleanup | post_cleanup | ⏳ PENDING |  |
| Execute post-upload delete plan | post_cleanup | ⏳ PENDING |  |
| Final remote snapshot | final_quota | ⏳ PENDING |  |
| Verify all cleanup and remote limits | final_quota | ⏳ PENDING |  |

#### Archive C — RUNNING

/srv/frigate → cloud:c

**Progress:** 7 / 16 steps settled (43.75%) · 5 succeeded · 0 failed · 2 skipped

**Active:** Upload files (copy)

**Individual cleanup rules — planning progress**

| Cleanup path | Settled | Pre-upload | Post-upload |
|:---|:---|:---|:---|
| cloud:c/ | 1 / 2 | SUCCESS | PENDING |
| cloud:c/Camera | 1 / 2 | SUCCESS | PENDING |

| Step | Phase | State | Detail |
|:---|:---|:---|:---|
| Pre-upload remote snapshot | reservation | ✅ SUCCESS |  |
| Acquire filtered local file snapshot | reservation | ✅ SUCCESS |  |
| Plan pre-upload cleanup: cloud:c/ | reservation | ✅ SUCCESS |  |
| Plan pre-upload cleanup: cloud:c/Camera | reservation | ✅ SUCCESS |  |
| Reserve space for selected upload files | reservation | ✅ SUCCESS |  |
| Execute pre-upload delete plan | reservation | ⏭ SKIPPED | No script-managed deletion/trash activity in this synthetic example |
| Pre-upload trash cleanup | reservation | ⏭ SKIPPED | No script-managed deletion/trash activity in this synthetic example |
| Upload files (copy) | upload | 🟢 RUNNING |  |
| Post-upload remote snapshot | post_cleanup | ⏳ PENDING |  |
| Plan post-upload cleanup: cloud:c/ | post_cleanup | ⏳ PENDING |  |
| Plan post-upload cleanup: cloud:c/Camera | post_cleanup | ⏳ PENDING |  |
| Plan post-upload total-size cleanup | post_cleanup | ⏳ PENDING |  |
| Execute post-upload delete plan | post_cleanup | ⏳ PENDING |  |
| Post-upload trash cleanup | post_cleanup | ⏳ PENDING |  |
| Final remote snapshot | final_quota | ⏳ PENDING |  |
| Verify all cleanup and remote limits | final_quota | ⏳ PENDING |  |

**Transferred:** Unknown

**Average speed:** Unknown

**Runtime:** 00:00:00

_Running is determined by the live heartbeat. Expired live sensors use the retained result unless a different unfinished run is known. Unknown progress stays unknown. Steps are settled workflow observations, including failures and skips; destination progress is tracked independently. Generic jobs without detailed progress use their configured item unit. Average speed is a run average._

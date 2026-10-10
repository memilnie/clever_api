# Changelog

## 0.6.0 (2026-10-10)
---
 - Keep completed charging sessions and each month's energy surcharge rate in Home Assistant's storage, so earlier sessions and rates remain available for statistics. The history is deleted when the account is removed.

## 0.5.0 (2026-10-10)
---
 - Add price figures for comparing Clever with other electricity prices: monthly subscription fee, energy surcharge this month, and effective price per kWh this month.
 - Change the default subscription fee to 849 DKK (Clever One with a home charger from 1 September 2026).
 - Add a read-only mode without switches and control actions. New accounts start read-only; existing entries keep their controls.
 - Poll live data every five minutes when no vehicle is connected, and back off after failed requests.
 - Rename the `enable_flex` fields to `departure_time` and `required_energy`. The earlier names remain accepted.
 - Add Danish translations, and translated names and descriptions for the actions.
 - Point the project links at this fork and stop the daily scheduled validation run.

## 0.4.0 (2026-08-24)
---
 - Expose active charging, vehicle connection, target energy, progress, duration, average power, expected completion, and vehicle battery level.
 - Document the observed Android mobile API and Firestore handles.

## 0.3.2 (2026-08-24)
---
 - Preserve existing entity IDs when migrating legacy entries.
 - Route control actions to a selected Clever account when multiple entries exist.

## 0.3.1 (2026-08-24)
---
 - Fix config-flow loading on Home Assistant 2026.6.

## 0.3.0 (2026-08-23)
---
 - Replace the retired email-link flow with Firebase email/password authentication and refresh-token rotation.
 - Migrate backend requests to Clever mobile API v6.
 - Read live home-charger state and active transactions from Cloud Firestore.
 - Add config-entry migration and reauthentication for existing installations.
 - Add mocked API/model tests and automated linting and compilation checks.

## 0.2.4.2 (2023-05-13)
---
 - Deleted a couple of unused fields in the UserInfoData model because of validation errors for some users. These fields were not at use in the integration.

## 0.2.4 (2023-04-22)
---
 - Reauth using options flow.

## 0.2.3 (2023-04-14)
---
 - Fixed bug where integration was out of function when Intelligent Opladning was disabled (unconfirmed if fixed as I can not reproduce)

## 0.2.2 (2023-04-07)
---
 - Now possible to add integration with intelligent opladning disabled

## 0.2.1 (2023-03-24)
---
 - Now possible to add integration without a homecharger
 - `sensor.clever_estimated_total_price_this_month` is no a combination of subscription fee and energitillæg. The energitillæg price without subscription fee is a attribute

# Clever API

Unofficial Home Assistant custom integration for Clever subscriptions and home
chargers. This project is not affiliated with or supported by Clever A/S.

This is a fork of [mindThomas/clever_api](https://github.com/mindThomas/clever_api)
that adds price figures for comparing Clever with other electricity prices, a
read-only mode, slower polling when no car is connected, and Danish translations.

The integration follows the API used by the current Clever Android app:

- Firebase email/password authentication and refresh tokens
- Clever mobile backend API v6
- Cloud Firestore live home-charger state and active transactions

## Prerequisite

Create or migrate your Clever account in the official Clever app first. Account
creation, email verification, SMS verification, and CRM claim migration are not
implemented in this integration.

## Installation and login

Add `https://github.com/memilnie/clever_api` to HACS as a custom repository of
type Integration, install a release, restart Home Assistant, and add
**Clever API** from **Settings → Devices & services**.

New accounts are set up **read-only**: Home Assistant only reads data, and no
switches or control actions are created. Turn this off under the integration's
options to add the preheat and boost switches and the intelligent-charging
actions. Entries created before 0.5.0 keep their controls.

Enter the email and password used in the Clever app. The password is used for
the initial Firebase login and is not stored in the Home Assistant config entry.
Home Assistant stores the resulting Firebase refresh token, which must be
treated as a sensitive credential.

Existing version 0.2 entries are migrated to the new entry format and prompt
for reauthentication because the former email-link token cannot be converted
to a Firebase refresh token.

## Entities

All accounts expose:

- Energy this month
- Energy surcharge (the rate in DKK/kWh)
- Energy surcharge this month (the rate times this month's consumption)
- Monthly subscription fee
- Estimated total price this month (subscription fee plus surcharge)
- Effective price per kWh this month

Accounts with a home charger additionally expose:

- Energy this month on the home charger
- Energy in the current charging session
- Target energy and progress for the current charging session
- Charging-session duration and average delivered power
- Expected charging completion and vehicle battery level when available
- Charging and vehicle-connected binary sensors
- Live charger state
- Intelligent-charging status and configuration attributes
- Preheat switch (not in read-only mode)
- Skip-intelligent-charging (boost) switch (not in read-only mode)

## Comparing Clever with other electricity prices

Clever charges a fixed monthly subscription fee and, in months where the
average purchase price of electricity is above 0.89 DKK/kWh, an **energy
surcharge** on every kWh charged at home and on the network. The surcharge rate
for the month comes from Clever. The subscription fee is not available from the
API, so it is a setting: the default is 849 DKK, the price of Clever One with a
home charger from 1 September 2026 (999 DKK without one).

**Effective price per kWh this month** puts those two costs on the same scale
as other electricity prices:

    (subscription fee × share of the month that has passed + surcharge so far)
    ÷ kWh charged so far

The fee is counted in proportion to the elapsed part of the month, so the
figure is meaningful from the first charge rather than only on the last day.
On the last day it equals the full month's cost per kWh. It is unknown until
something has been charged in the month.

With a home charger, the electricity itself goes through your own meter and
Clever refunds it at a monthly rate. That refund and your own electricity bill
are not part of these figures. The effective price is therefore what Clever
costs on top, on the assumption that the refund covers what the electricity
cost you.

## Charging history

Completed charging sessions (start, stop, energy, charge point and whether it
was the home charger) and each month's energy surcharge rate are kept in
`.storage/clever_api.history.<entry id>` in the Home Assistant configuration
folder. Sessions are added as Clever reports them and are kept after they drop
out of Clever's response. The file is deleted when the account is removed from
Home Assistant.

## Actions

The actions are only available when at least one account is not read-only.

- `clever_api.enable_flex` sets `departure_time` (hh:mm) and `required_energy`
  (kWh), then enables the home charging profile.
- `clever_api.disable_flex` disables the home charging profile.

The earlier field names `depature_time` and `desired_range` and the legacy
`phase_count` input remain accepted so existing automations keep working. API
v6 does not expose a charging-profile setting for phase count.

## Update intervals

Consumption, surcharge, installations, and charging profiles refresh hourly.

Live chargepoint state and transaction documents refresh every minute while a
vehicle is on the home charger, and every five minutes otherwise. A newly
connected vehicle can therefore take up to five minutes to appear. After a
failed request the wait doubles for each consecutive failure, up to 30 minutes.

## Limitations

- Only the first home installation is currently represented.
- Monthly consumption is attributed to the month in which a charging session
  started, matching the earlier integration behavior.
- Clever may change the private mobile API without notice.
- Control actions depend on an active and supported Clever home-charging
  profile.
- The active-transaction API does not expose instantaneous charging power. The
  power sensor is the session average calculated from delivered energy and
  elapsed time; it must not be interpreted as the current charger output.

See [API.md](API.md) for the observed mobile API and Firestore handles.

Use this integration at your own risk and comply with the applicable Clever
terms and account policies.

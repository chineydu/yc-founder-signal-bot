# Submission evidence: founder-announcement examples

## Important evidence note

These are **retrospective validation examples**, based on real public founder posts. They should not be described as live detections by the bot unless the corresponding Slack alert was actually produced by a monitoring run.

The purpose of this file is to document real-world inputs the social-signal detector is designed to recognize and to provide verifiable source material for the submission.

## Example 1 — Understudy Labs

**Founder:** Luis Manrique (with Aamir Poonawalla)

**Batch:** YC S26

**Source:** LinkedIn

**Founder announcement:** Luis Manrique publicly wrote that he and Aamir had been accepted into Y Combinator's Summer Cohort and were going to build Understudy Labs (YC S26).

**Source post:** https://www.linkedin.com/posts/luismanrique_understudy-is-joining-yc-s26-activity-7475576610868387840--bU7

**YC confirmation:** https://www.ycombinator.com/companies/understudy-labs

**Expected alert format:**

> EARLY YC SIGNAL — Founder Announced Before YC
>
> Company: Understudy Labs
> Founder: Luis Manrique / Aamir Poonawalla
> Batch: YC S26
> Source: LinkedIn
> Status: Founder/social announcement detected; not yet confirmed by YC Directory
> Original post: https://www.linkedin.com/posts/luismanrique_understudy-is-joining-yc-s26-activity-7475576610868387840--bU7
>
> Note: when replaying this historical example after YC confirmation, label the Slack message as a retrospective replay rather than a new live discovery.

## Example 2 — Tracer

**Founder:** Adam Rida

**Batch:** YC S26

**Source:** LinkedIn

**Founder announcement:** Adam Rida publicly announced that he was joining the Y Combinator S26 batch as a solo founder and named Tracer (YC S26).

**Source post:** https://www.linkedin.com/posts/adam-rida-581296142_excited-to-share-that-im-joining-the-y-combinator-activity-7477869892688388097-MyCE

**YC confirmation:** https://www.ycombinator.com/companies/industry/open-source

**Expected alert format:**

> EARLY YC SIGNAL — Founder Announced Before YC
>
> Company: Tracer
> Founder: Adam Rida
> Batch: YC S26
> Source: LinkedIn
> Status: Founder/social announcement detected; not yet confirmed by YC Directory
> Original post: https://www.linkedin.com/posts/adam-rida-581296142_excited-to-share-that-im-joining-the-y-combinator-activity-7477869892688388097-MyCE
>
> Note: when replaying this historical example after YC confirmation, label the Slack message as a retrospective replay rather than a new live discovery.

## Submission wording

For the final application, use these examples as **supporting validation material**, not as proof of a live detection event unless a corresponding Slack screenshot exists. The live Slack screenshot in the submission should be explicitly described as a controlled delivery demonstration if it was produced using the workflow's test mode.

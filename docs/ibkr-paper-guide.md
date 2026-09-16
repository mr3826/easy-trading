# IBKR Paper Boundary Guide

## Current Status

The repository contains a safe `IBKRPaperBrokerAdapter` boundary, not a claim
of IBKR connectivity. `start()` returns unavailable until an approved external
paper client is configured. No account, credential, or order is included here.

## External Setup Required

An operator must provide an IBKR paper account, approved TWS or Gateway paper
connection, network access, and a test window. The account and port must be
verified as paper before any connection test.

External tests must be selected explicitly, skipped by default, reject live
ports and accounts, and require a second explicit submission authorization.
The first connectivity test should perform no order submission.

## Lifecycle Contract

The adapter boundary covers connection and reconnect lifecycle, next-valid
order ID, submission, cancellation, replacement, acknowledgements, status
updates, partial fills, commissions, rejections, account snapshots, positions,
open/completed orders, parent-child protection, OCA behavior, and
reconciliation. The current implementation remains a non-connecting boundary
until those operations are supplied by the external client integration.

## Prohibitions

Never use live credentials, live ports, or a live account. Never infer paper
validation from fake-adapter tests. No IBKR connection or order submission has
occurred as part of this repository verification.

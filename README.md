# Google Maps List Copy

Google Maps lets people save places to lists and collaborate on shared lists, but it does not currently provide a way to duplicate a list. This is especially frustrating when someone wants to leave a shared list while keeping their own copy of the places in it.

Google Maps List Copy aims to provide a script that copies the saved places from one Google Maps list into a new list owned by the user.

## Goals

- Authenticate securely with Google.
- Discover the Google Maps saved-place lists available to the user.
- Read the places contained in a selected list.
- Create a new list containing copies of those places.

## Project status

This project is at the planning stage. The initial implementation work is tracked in the repository's GitHub issues.

## Important constraint

Google does not currently expose a documented public API for all Google Maps saved-list operations. The project will begin by investigating the supported authentication and access options before committing to an implementation approach.

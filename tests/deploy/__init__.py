"""The deploy test package: the LaunchAgent template and the converge gate.

Separate from `tests/scaffold/` because these are the two artifacts that govern
*deployment*, and a reader looking for "what stops a bad release" should find
them together. Neither test starts a service, binds a port, writes to the vault,
or bootstraps a LaunchAgent — the whole point of the converge gate is that it
runs read-only against an already-running service, and its tests prove that by
substituting the one function that touches the network.
"""

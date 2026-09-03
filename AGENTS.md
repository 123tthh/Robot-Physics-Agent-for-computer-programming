# Repository Guidance

## Purpose

This repository implements a verifier-grounded Agent–Skill runtime for humanoid physical computer use. Preserve the separation between high-level planning, finite-horizon skills, independent verification, and low-level safety control.

## Design rules

- The Agent may call L2/L3 skills; it must not emit joint commands.
- A controller may claim a state, but only a Verifier may mark derived task state as verified.
- Every skill has preconditions, postconditions, resource requirements, failure codes, and a version.
- Hardware, perception, code-agent, and test implementations stay behind adapter interfaces.
- Simulation and real-robot skills must expose the same contract.
- Never treat a language-model completion message as proof that a software task passed.
- Keep safety checks independent from the Agent and learned policies.

## Development

Run before committing:

```bash
make check
make demo
```

Use Python standard-library components in the core package where practical. Add ROS 2, model, or vendor dependencies through optional adapters rather than importing them into domain models.

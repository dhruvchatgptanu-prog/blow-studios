"""Quality control on the actual rendered video and audio.

Automatic QA is probabilistic: deterministic measurements cover measurable
properties, telemetry and pixel checks cover motion, and an optional
multimodal review covers perceptual properties. Every check reports what it
measured, how confident it is and how to repair a failure.
"""

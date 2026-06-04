import matplotlib.pyplot as plt
import matplotlib.patches as patches

fig, ax = plt.subplots(1, 1, figsize=(4, 4))

# Draw the L-shaped figure (right angle / coordinate axes fragment)
# Vertical line segment
ax.plot([2, 2], [1, 4], color='gray', linewidth=2)

# Horizontal line segment
ax.plot([0, 2], [1, 1], color='gray', linewidth=2)

# Draw the loop/curve on the right side (resembles a 'g' or loop shape)
# This appears to be a small loop attached to the top-right of the vertical line
import numpy as np

# Loop shape at top right
theta = np.linspace(0, 2 * np.pi, 200)
loop_cx, loop_cy = 2.6, 3.2
loop_rx, loop_ry = 0.35, 0.5
loop_x = loop_cx + loop_rx * np.cos(theta)
loop_y = loop_cy + loop_ry * np.sin(theta)
ax.plot(loop_x, loop_y, color='gray', linewidth=2)

# Tail going down from the loop
tail_x = np.array([loop_cx + 0.0, loop_cx + 0.05, loop_cx + 0.1])
tail_y = np.array([loop_cy - loop_ry, loop_cy - loop_ry - 0.3, loop_cy - loop_ry - 0.5])
ax.plot(tail_x, tail_y, color='gray', linewidth=2)

ax.set_xlim(-0.5, 4)
ax.set_ylim(0, 5)
ax.axis('off')

plt.tight_layout()
plt.savefig(OUTPUT_PATH, dpi=150, bbox_inches='tight')
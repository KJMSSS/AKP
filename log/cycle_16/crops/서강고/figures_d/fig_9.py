import matplotlib.pyplot as plt
import numpy as np

plt.rcParams['font.family'] = 'Malgun Gothic'

fig, ax = plt.subplots(1, 1, figsize=(6, 6))

# --- Axes ---
ax.axhline(0, color='black', linewidth=1.2)
ax.axvline(0, color='black', linewidth=1.2)

# Arrow tips
ax.annotate('', xy=(5.5, 0), xytext=(5.3, 0),
            arrowprops=dict(arrowstyle='->', color='black', lw=1.2))
ax.annotate('', xy=(0, 5.5), xytext=(0, 5.3),
            arrowprops=dict(arrowstyle='->', color='black', lw=1.2))

# --- Logarithm curves ---
# y = log_2(x+a) type curve passing through A
# Let A be at approximately (3, 4) based on the image
# Curve 1: looks like y = log_2(x) shifted — passing through A(3,4) and B(1,1)
# We'll use two log curves

x1 = np.linspace(0.05, 5.5, 400)
# Curve that passes through A ~ (3, 4): try y = 2*log2(x+1)
# log2(x+1)*2: at x=3 -> 2*log2(4)=4 ✓, at x=1 -> 2*log2(2)=2
curve1_y = 2 * np.log2(x1 + 1)
ax.plot(x1, curve1_y, color='black', linewidth=1.5)

# Second curve: steeper, like y = log_2(x) * something or different base
# passes through same point A and curves differently
# Try y = 3*log2(x+0.5): at x=3 -> 3*log2(3.5) ~ 3*1.807=5.4, too high
# Try y = 2.5*log2(x+0.5): at x=3 -> 2.5*log2(3.5)~4.52
# Let's place A at intersection: solve 2*log2(x+1) = k*log2(x+c)
# From the image, A appears to be near top-right where two curves meet
# and B is lower where lines intersect

# Let A = (3, 4) and use curve: y = 2*log2(x+1) (already done)
# Second curve passing through A(3,4): y = a*log_3(x+b)
# Use y = 3*log2(x) : at x=3 -> 3*1.585=4.75, close
# Use y = 2.8*log2(x): at x=3 -> 2.8*1.585=4.44
# Let's pick intersection: 2*log2(x+1) = c*log2(x)
# At x=3: 4 = c*log2(3) -> c = 4/1.585 = 2.52

x2 = np.linspace(0.1, 5.5, 400)
c2 = 4 / np.log2(3)
curve2_y = c2 * np.log2(x2)
ax.plot(x2, curve2_y, color='black', linewidth=1.5)

# Mark point A (intersection of two curves near (3,4))
A = (3, 4)
ax.plot(*A, 'k*', markersize=10, zorder=5)
ax.text(A[0] + 0.1, A[1] + 0.05, 'A', fontsize=11, fontweight='bold')

# --- Point B ---
# B is lower intersection, approximately at (1, 1.5) ~ on curve1
# curve1 at x=1: 2*log2(2)=2 → B ~ (1, 2)
B = (1, 2)
ax.plot(*B, 'k*', markersize=10, zorder=5)
ax.text(B[0] - 0.3, B[1] - 0.25, 'B', fontsize=11, fontweight='bold')

# --- Lines from B to A and other lines ---
# Line from origin O(0,0) to A(3,4)
ox, oy = np.array([0, A[0]]), np.array([0, A[1]])
ax.plot(ox, oy, color='black', linewidth=1.3)

# Line from B to A
ax.plot([B[0], A[0]], [B[1], A[1]], color='black', linewidth=1.3)

# Line from B downward to x-axis (vertical-ish)
ax.plot([B[0], B[0]], [B[1], 0], color='black', linewidth=1.0, linestyle='--')

# Another line from B going left-down (like in image)
# steep line through B going toward lower-left
t = np.linspace(-0.5, 1.2, 100)
slope_line = 3.5
bx, by = B
line_x = bx + t
line_y = by + slope_line * t
ax.plot(line_x, line_y, color='black', linewidth=1.2)

# Line from O to B
ax.plot([0, B[0]], [0, B[1]], color='black', linewidth=1.0, linestyle='-')

# --- Labels on axes ---
ax.text(5.4, -0.25, 'x', fontsize=12)
ax.text(0.1, 5.4, 'y', fontsize=12)
ax.text(-0.2, -0.2, 'O', fontsize=11)

# --- Annotations for curve equations ---
ax.text(0.5, 3.8, r'$y = \log_2(x+a)$', fontsize=9, rotation=60)
ax.text(3.5, 2.5, r'$x - a$', fontsize=10)

# Bottom labels
ax.text(0.5, -0.5, r'$R = 3a$', fontsize=8)
ax.text(-0.5, -0.7, r'$R = a\log_2^3$', fontsize=8)

# Mark a on x-axis
ax.text(B[0] - 0.05, -0.25, 'a', fontsize=10)

# Circle annotation (number 2 circled) - represented as text
ax.text(1.2, 2.8, '②', fontsize=18, color='black',
        bbox=dict(boxstyle='circle,pad=0.3', edgecolor='black', facecolor='white'))

# --- Axis settings ---
ax.set_xlim(-0.7, 5.8)
ax.set_ylim(-1.0, 5.8)
ax.axis('off')

# Draw clean axis lines manually (already done via axhline/axvline)
# Redraw for clarity
ax.annotate('', xy=(5.6, 0), xytext=(-0.5, 0),
            arrowprops=dict(arrowstyle='->', color='black', lw=1.2))
ax.annotate('', xy=(0, 5.6), xytext=(0, -0.8),
            arrowprops=dict(arrowstyle='->', color='black', lw=1.2))

plt.tight_layout()
plt.savefig(OUTPUT_PATH, dpi=150, bbox_inches='tight')
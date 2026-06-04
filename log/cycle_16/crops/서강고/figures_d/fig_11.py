import matplotlib.pyplot as plt
import matplotlib.patches as patches

fig, ax = plt.subplots(1, 1, figsize=(8, 3))
ax.axis('off')

# Display the mathematical expressions from the image
ax.text(0.35, 0.65, r'$3^n$', fontsize=36, ha='center', va='center',
        transform=ax.transAxes, fontweight='normal')

ax.text(0.42, 0.65, r'$\cdot$', fontsize=36, ha='center', va='center',
        transform=ax.transAxes)

ax.text(0.55, 0.65, r'$n = 4$', fontsize=32, ha='center', va='center',
        transform=ax.transAxes, fontweight='normal')

ax.text(0.88, 0.70, r'$\geq$', fontsize=28, ha='center', va='center',
        transform=ax.transAxes)

ax.text(0.90, 0.45, r'$h=$', fontsize=28, ha='center', va='center',
        transform=ax.transAxes)

plt.tight_layout()
plt.savefig(OUTPUT_PATH, dpi=150, bbox_inches='tight')
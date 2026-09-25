#!/usr/bin/env python3
"""Plot measured sitting evidence. Optional matplotlib; no Isaac import."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    report=json.loads(args.report.read_text())
    rows=report['samples']
    if not rows:raise ValueError('no physical observations')
    t=[row['time_s'] for row in rows]
    mass=report['robot_mass_kg'];weight=mass*9.81
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,axs=plt.subplots(2,2,figsize=(12,7),layout='constrained')
    fig.suptitle('G1 + dual Dex5: measured free-base sitting evidence',fontsize=16,fontweight='bold')
    for ax in axs.flat:
        ax.axvspan(2.5,3.5,color='#dae8fa',alpha=.5)
        ax.axvspan(t[-1]-2,t[-1],color='#d3efdb',alpha=.7)
        ax.grid(alpha=.18);ax.set_xlabel('Simulation time (s)')
    ax=axs[0,0]
    ax.plot(t,[r['pelvis_xyz_m'][2] for r in rows],label='Measured pelvis height',color='#1765b0')
    ax.set_title('Stand, descend, maintain contact');ax.set_ylabel('Height (m)');ax.legend()
    ax=axs[0,1]
    for key,label,color in [('seat_force_n','Seat','#137f56'),('left_foot_force_n','Left foot','#1765b0'),('right_foot_force_n','Right foot','#d07818')]:
        ax.plot(t,[r[key] for r in rows],label=label,color=color,linewidth=1.)
    ax.axhline(.15*weight,color='#137f56',linestyle=':',label='Seat acceptance minimum')
    ax.set_title('Measured upward contact forces');ax.set_ylabel('Force (N)');ax.legend(fontsize=8)
    ax=axs[1,0]
    for key,label,color in [('pelvis_speed_mps','Pelvis','#1765b0'),('chair_speed_mps','Passive chair','#137f56')]:
        ax.plot(t,[r[key] for r in rows],label=label,color=color,linewidth=1.)
    ax.axhline(.06,color='#1765b0',linestyle=':',linewidth=1.);ax.axhline(.04,color='#137f56',linestyle=':',linewidth=1.)
    ax.set_title('Speed; thresholds apply to final green window');ax.set_ylabel('Speed (m/s)');ax.legend()
    ax=axs[1,1]
    ax.plot(t,[r['torso_tilt_rad'] for r in rows],label='Torso',color='#1765b0')
    ax.plot(t,[r['chair_tilt_rad'] for r in rows],label='Chair',color='#137f56')
    ax.axhline(.30,color='#1765b0',linestyle=':',linewidth=1.);ax.axhline(.12,color='#137f56',linestyle=':',linewidth=1.)
    ax.set_title('Tilt from vertical');ax.set_ylabel('Angle (rad)');ax.legend()
    fig.supxlabel('Blue window: standing precondition  |  Green: independent seated verification  |  PhysX oracle, simulation only',fontsize=9)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(args.output,dpi=160)
    print(args.output)


if __name__=='__main__':main()

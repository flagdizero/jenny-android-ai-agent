#!/bin/sh
# gesti: drag x0 y0 x1 y1 [passi]  → press-hold-drag senza fling (pausa prima dell'UP)
export ANDROID_SERIAL="${ANDROID_SERIAL:?il seriale del telefono di prova, da adb devices -l}"
drag() { x0=$1; y0=$2; x1=$3; y1=$4; n=${5:-12}
  adb shell input motionevent DOWN $x0 $y0; sleep 0.15
  i=1; while [ $i -le $n ]; do
    x=$((x0 + (x1-x0)*i/n)); y=$((y0 + (y1-y0)*i/n))
    adb shell input motionevent MOVE $x $y; i=$((i+1)); done
  sleep 0.4; adb shell input motionevent UP $x1 $y1; }
"$@"

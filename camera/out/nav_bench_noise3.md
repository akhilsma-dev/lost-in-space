# Position from planets: offline benchmark [noise3] (24 positions, 0.5 min wall)

Epoch TDB JD 2461352.5. Hidden observer: 0.7-2 AU from the Sun, ecliptic latitude +/-3 deg, more than 0.05 AU from any planet. Bodies: Mercury, Venus, Earth, Moon, Mars, Jupiter and Saturn barycentres (DE421), visible if mag <= 5.5 and inside one of 8 ecliptic scan frames (1920x1080, 50 deg FOV, boresights at Sun elongation 40, 80, ..., 320 deg; no pointing within 35 deg of the Sun).

- **geometry**: exact light-time-corrected directions + Gaussian noise sigma = 20 arcsec per axis, shuffled, then identified and solved (checks the maths without the renderer).
- **rendered**: each scan frame is rendered by sim.sky with the planets as extra blobs (noise 3 DN), solved by tetra3 (same centroider/solver settings as solve_live), blobs more than 4 px from every catalogue star (mag <= 6.5) are planet candidates, merged across overlapping frames, identified and solved.
- **+Sun**: a Sun direction (sun sensor, sigma 20 arcsec) is added as a known body at the origin.
- fix = least squares weighted by 1/(sigma_i r_i)^2, light-time corrected. Ellipsoid = 1-sigma semi-axes of (sum (I - u u^T)/(sigma_i r_i)^2)^-1 with sigma = 20 arcsec (geometry), 5 arcsec (rendered planets), 20 arcsec (Sun). NEES = e^T C^-1 e (should average ~3 if the ellipsoid is honest).
- ID ok = every assigned candidate got its true body (candidates labelled by the nearest true body within 0.1 deg).

| mode | Sun | fixes | median err km | median err AU | 90% err km | median ellipsoid a/b/c km | mean NEES | ID ok | bodies used (median) | bodies visible (median) |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| rendered | no | 24/24 | 7,564 | 5.06e-05 | 18,933 | 20,648 / 3,807 / 3,644 | 2.3 | 24/24 | 4 | 5 |
| rendered | yes | 24/24 | 11,851 | 7.92e-05 | 22,082 | 16,242 / 3,768 / 3,587 | 2.6 | 24/24 | 5 | 5 |

Rendered pipeline: 192/192 scan frames locked, median attitude error 14 arcsec from tetra3, 3.3 arcsec after the least-squares refit (total rotation, roll included); median planet-candidate direction error 3.1 arcsec; median 5 planet candidates per scan, 4 of them real planets.

## Per position (rendered, planets only)

| case | r AU | visible | used | err km | ellipsoid a/b/c km | NEES | resid RMS arcsec | ID ok |
|---:|---:|---|---|---:|---|---:|---:|---|
| 0 | 1.17 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 6,525 | 24,203 / 8,179 / 7,749 | 0.6 | 2 | yes |
| 1 | 1.15 | Venus+Earth+Moon+Mars+Saturn | Earth+Moon+Venus+Saturn+Mars | 17,104 | 20,523 / 3,595 / 3,541 | 6.2 | 5 | yes |
| 2 | 1.18 | Earth+Moon+Mars+Jupiter+Saturn | Mars+Saturn+Jupiter+Earth+Moon | 2,308 | 6,650 / 1,371 / 1,343 | 2.3 | 3 | yes |
| 3 | 0.99 | Earth+Moon+Jupiter+Saturn | Earth+Moon+Jupiter+Saturn | 15,227 | 21,746 / 6,752 / 6,448 | 3.7 | 3 | yes |
| 4 | 1.35 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Saturn+Jupiter+Venus+Mars | 44,663 | 20,729 / 3,351 / 3,308 | 8.1 | 4 | yes |
| 5 | 0.80 | Venus+Earth+Moon+Mars+Jupiter | Earth+Moon+Venus+Jupiter+Mars | 3,518 | 8,777 / 3,604 / 3,334 | 0.9 | 2 | yes |
| 6 | 0.81 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Jupiter+Venus+Mercury+Mars+Saturn | 1,616 | 3,554 / 2,093 / 1,804 | 0.6 | 3 | yes |
| 7 | 1.08 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Jupiter+Mercury+Venus+Mars+Saturn | 2,797 | 2,575 / 1,950 / 1,554 | 1.3 | 5 | yes |
| 8 | 1.10 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 15,842 | 25,823 / 8,425 / 8,011 | 0.4 | 3 | yes |
| 9 | 1.26 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Jupiter+Saturn+Venus+Mars | 8,314 | 17,873 / 2,753 / 2,721 | 2.0 | 4 | yes |
| 10 | 1.87 | Venus+Earth+Moon+Jupiter+Saturn | Earth+Moon+Venus+Jupiter+Saturn | 16,691 | 24,102 / 3,783 / 3,738 | 4.7 | 5 | yes |
| 11 | 1.03 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Jupiter+Saturn+Venus+Mars+Earth+Moon | 1,332 | 2,821 / 1,078 / 1,009 | 0.2 | 1 | yes |
| 12 | 1.15 | Venus+Earth+Moon+Mars+Saturn | Earth+Moon+Saturn+Venus+Mars | 7,189 | 20,567 / 3,603 / 3,549 | 3.2 | 5 | yes |
| 13 | 0.91 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 5,741 | 23,518 / 7,425 / 7,081 | 0.2 | 4 | yes |
| 14 | 1.29 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Saturn+Mars | 30,374 | 28,412 / 3,308 / 3,286 | 2.8 | 1 | yes |
| 15 | 1.80 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 19,716 | 29,270 / 10,522 / 9,902 | 1.7 | 1 | yes |
| 16 | 1.02 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 7,010 | 27,001 / 8,510 / 8,117 | 0.4 | 2 | yes |
| 17 | 0.90 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 4,632 | 23,662 / 7,442 / 7,099 | 0.4 | 2 | yes |
| 18 | 1.67 | Venus+Earth+Moon+Mars+Saturn | Earth+Moon+Venus+Saturn+Mars | 14,469 | 25,688 / 4,384 / 4,322 | 1.9 | 6 | yes |
| 19 | 1.41 | Venus+Earth+Moon+Mars+Jupiter | Earth+Moon+Venus+Jupiter+Mars | 4,444 | 10,034 / 4,360 / 3,999 | 1.1 | 6 | yes |
| 20 | 1.11 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Venus+Mars+Jupiter+Mercury+Saturn | 7,938 | 4,586 / 2,673 / 2,309 | 3.8 | 4 | yes |
| 21 | 1.26 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Venus+Jupiter+Mars+Saturn | 11,358 | 8,119 / 3,831 / 3,465 | 5.3 | 4 | yes |
| 22 | 1.53 | Venus+Earth+Moon+Mars+Jupiter | Earth+Moon+Venus+Jupiter+Mars | 5,948 | 10,180 / 4,458 / 4,084 | 1.5 | 3 | yes |
| 23 | 1.07 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 11,659 | 25,469 / 8,270 / 7,867 | 0.9 | 2 | yes |

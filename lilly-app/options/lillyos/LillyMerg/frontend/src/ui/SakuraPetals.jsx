import { useEffect, useRef } from 'react';

const PETAL_COUNT = 18;

function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}

export default function SakuraPetals() {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    let animId;

    const petals = Array.from({ length: PETAL_COUNT }, () => ({
      x: randomBetween(0, window.innerWidth),
      y: randomBetween(-50, -20),
      size: randomBetween(6, 14),
      speedY: randomBetween(0.3, 0.8),
      speedX: randomBetween(-0.2, 0.2),
      rotation: randomBetween(0, Math.PI * 2),
      rotSpeed: randomBetween(-0.01, 0.01),
      opacity: randomBetween(0.3, 0.6),
      sway: randomBetween(0, Math.PI * 2),
      swaySpeed: randomBetween(0.005, 0.015),
      swayAmplitude: randomBetween(10, 30),
    }));

    function resize() {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    function drawPetal(p) {
      const x = p.x + Math.sin(p.sway) * p.swayAmplitude;
      ctx.save();
      ctx.translate(x, p.y);
      ctx.rotate(p.rotation);
      ctx.globalAlpha = p.opacity;
      ctx.beginPath();
      ctx.moveTo(0, -p.size / 2);
      ctx.bezierCurveTo(
        p.size / 2, -p.size / 2,
        p.size / 2, p.size / 2,
        0, p.size / 2
      );
      ctx.bezierCurveTo(
        -p.size / 2, p.size / 2,
        -p.size / 2, -p.size / 2,
        0, -p.size / 2
      );
      ctx.fillStyle = '#F9A8D4';
      ctx.fill();
      ctx.restore();
    }

    function animate() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      for (const p of petals) {
        p.y += p.speedY;
        p.x += p.speedX;
        p.rotation += p.rotSpeed;
        p.sway += p.swaySpeed;
        if (p.y > canvas.height + 20) {
          p.y = randomBetween(-30, -10);
          p.x = randomBetween(0, canvas.width);
        }
        if (p.x < -50) p.x = canvas.width + 20;
        if (p.x > canvas.width + 50) p.x = -20;
        drawPetal(p);
      }
      animId = requestAnimationFrame(animate);
    }
    animate();

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', resize);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        width: '100vw',
        height: '100vh',
        pointerEvents: 'none',
        zIndex: 9999,
      }}
    />
  );
}

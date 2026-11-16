from __future__ import annotations

import math
from collections.abc import Iterable


class Point3D:
	def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
		self.x, self.y, self.z = x, y, z

	@staticmethod
	def create(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Point3D:
		return Point3D(x, y, z)

	def copy(self) -> Point3D:
		return Point3D(self.x, self.y, self.z)

	def transformBy(self, m: Matrix3D) -> bool:
		a = m.a
		x, y, z = self.x, self.y, self.z
		self.x = a[0] * x + a[1] * y + a[2] * z + a[3]
		self.y = a[4] * x + a[5] * y + a[6] * z + a[7]
		self.z = a[8] * x + a[9] * y + a[10] * z + a[11]
		return True

	def distanceTo(self, o: Point3D) -> float:
		return math.dist((self.x, self.y, self.z), (o.x, o.y, o.z))


class Vector3D(Point3D):
	@staticmethod
	def create(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Vector3D:
		return Vector3D(x, y, z)

	def transformBy(self, m: Matrix3D) -> bool:
		a = m.a
		x, y, z = self.x, self.y, self.z
		self.x = a[0] * x + a[1] * y + a[2] * z
		self.y = a[4] * x + a[5] * y + a[6] * z
		self.z = a[8] * x + a[9] * y + a[10] * z
		return True


class Matrix3D:
	def __init__(self, a: Iterable[float] | None = None) -> None:
		self.a = list(a) if a else [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]

	@staticmethod
	def create() -> Matrix3D:
		return Matrix3D()

	def copy(self) -> Matrix3D:
		return Matrix3D(self.a)

	def transformBy(self, m: Matrix3D) -> bool:
		A = [self.a[i * 4 : i * 4 + 4] for i in range(4)]
		B = [m.a[i * 4 : i * 4 + 4] for i in range(4)]
		C = [[sum(B[i][k] * A[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
		self.a = [v for r in C for v in r]
		return True

	def asArray(self) -> list[float]:
		return list(self.a)

	def getAsCoordinateSystem(self) -> tuple[Point3D, Vector3D, Vector3D, Vector3D]:
		a = self.a
		return (
			Point3D(a[3], a[7], a[11]),
			Vector3D(a[0], a[4], a[8]),
			Vector3D(a[1], a[5], a[9]),
			Vector3D(a[2], a[6], a[10]),
		)


class Application:
	_app: Application | None = None

	@staticmethod
	def get() -> Application | None:
		return Application._app


def __getattr__(name: str) -> type:
	cls = type(name, (), {})
	globals()[name] = cls
	return cls

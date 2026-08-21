#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
结算审核智能体 - 系统验证脚本
快速验证三个核心文件的完整性和功能
"""

import sys
import os

def check_files():
    """检查核心文件是否存在"""
    print("=" * 80)
    print("第一步：检查核心文件")
    print("=" * 80)

    required_files = [
        'settlement_audit_engine.py',
        'app.py',
        'settlement_platform.html'
    ]

    all_exist = True
    for filename in required_files:
        exists = os.path.exists(filename)
        status = "✓" if exists else "✗"
        print(f"  {status} {filename:<30} {'存在' if exists else '缺失'}")
        if not exists:
            all_exist = False

    print()
    return all_exist

def test_engine():
    """测试计算引擎"""
    print("=" * 80)
    print("第二步：测试计算引擎（11个核心模型）")
    print("=" * 80)

    try:
        from settlement_audit_engine import SettlementAuditEngine

        # 初始化引擎
        engine = SettlementAuditEngine()
        print("  ✓ 引擎初始化成功")

        # 导入示例数据
        result = engine.import_sample_data()
        print(f"  ✓ 示例数据导入: {result['message']}")

        # 测试模型1.1
        model_result = engine.model_1_1_authorization_rate_deviation('C2024001')
        print(f"  ✓ 模型1.1测试: 确权率 {model_result.get('authorization_rate', 0)}%")

        # 测试模型1.2
        model_result = engine.model_1_2_delay_settlement_time_loss('C2024002')
        print(f"  ✓ 模型1.2测试: 延迟 {model_result.get('delay_days', 0)}天")

        # 测试模型2.1
        model_result = engine.model_2_1_exceed_contract_5_percent('C2024003')
        print(f"  ✓ 模型2.1测试: 超合同比例 {model_result.get('exceed_ratio', 0)}%")

        # 测试三表比对
        comparison = engine.three_table_comparison('C2024001')
        print(f"  ✓ 三表比对测试: 利润率 {comparison.get('profit_rate', 0)}%")

        # 测试真问题包生成
        issue_package = engine.generate_true_issue_package('C2024003')
        print(f"  ✓ 真问题包生成: 发现 {issue_package.get('total_issues', 0)} 个疑点")

        engine.close()
        print()
        return True

    except ImportError as e:
        print(f"  ✗ 依赖包缺失: {e}")
        print(f"  解决方案: pip install duckdb pandas")
        print()
        return False
    except Exception as e:
        print(f"  ✗ 测试失败: {e}")
        print()
        return False

def test_html():
    """测试前端HTML文件"""
    print("=" * 80)
    print("第三步：测试前端HTML文件")
    print("=" * 80)

    try:
        with open('settlement_platform.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查关键元素
        checks = [
            ('<!DOCTYPE html>', 'HTML文档声明'),
            ('中建集团结算审核智能体', '页面标题'),
            ('--primary: #0080cc', '中建标准蓝色变量'),
            ('view-overview', '主审驾驶舱视图'),
            ('view-compare', '三表穿透视图'),
            ('view-issues', '疑点追踪视图'),
            ('view-experts', '专家会审视图'),
            ('view-closure', '整改销号视图'),
            ('modal-split-screen', '双栏穿透弹窗'),
            ('modal-closure', '凭证卡口弹窗'),
            ('API_BASE', 'API基地址配置'),
            ('fetchDashboardStats', '统计数据获取函数'),
        ]

        all_passed = True
        for keyword, description in checks:
            exists = keyword in content
            status = "✓" if exists else "✗"
            print(f"  {status} {description:<30}")
            if not exists:
                all_passed = False

        file_size = len(content) / 1024
        print(f"\n  文件大小: {file_size:.1f} KB")
        print()
        return all_passed

    except Exception as e:
        print(f"  ✗ 测试失败: {e}")
        print()
        return False

def test_api_routes():
    """测试API路由定义"""
    print("=" * 80)
    print("第四步：测试API路由定义")
    print("=" * 80)

    try:
        with open('app.py', 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查关键路由
        routes = [
            ('@app.route(\'/\')', '前端页面托管'),
            ('@app.route(\'/api/init\'', '数据初始化'),
            ('@app.route(\'/api/dashboard/stats\'', '统计面板'),
            ('@app.route(\'/api/models/1.1', '模型1.1接口'),
            ('@app.route(\'/api/comparison', '三表比对'),
            ('@app.route(\'/api/issues/list\'', '疑点清单'),
            ('@app.route(\'/api/experts/discuss\'', '专家会审'),
            ('@app.route(\'/api/review/decision\'', '主审裁决'),
            ('@app.route(\'/api/remediation/close\'', '整改销号'),
            ('@app.route(\'/api/knowledge/query\'', '知识检索'),
        ]

        all_passed = True
        for route_def, description in routes:
            exists = route_def in content
            status = "✓" if exists else "✗"
            print(f"  {status} {description:<30}")
            if not exists:
                all_passed = False

        # 检查端口配置
        if 'port=5100' in content:
            print(f"  ✓ 端口配置: 5100")
        else:
            print(f"  ⚠ 端口配置未找到，请检查")

        print()
        return all_passed

    except Exception as e:
        print(f"  ✗ 测试失败: {e}")
        print()
        return False

def main():
    """主函数"""
    print()
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 20 + "中建集团结算审核智能体 - 系统验证" + " " * 20 + "║")
    print("╚" + "=" * 78 + "╝")
    print()

    results = []

    # 执行所有测试
    results.append(("文件检查", check_files()))
    results.append(("计算引擎", test_engine()))
    results.append(("前端界面", test_html()))
    results.append(("API路由", test_api_routes()))

    # 汇总结果
    print("=" * 80)
    print("验证结果汇总")
    print("=" * 80)

    all_passed = True
    for name, passed in results:
        status = "✓ 通过" if passed else "✗ 失败"
        print(f"  {name:<20} {status}")
        if not passed:
            all_passed = False

    print()
    print("=" * 80)

    if all_passed:
        print("✓ 系统验证完成！所有检查通过。")
        print()
        print("下一步操作：")
        print("  1. 双击运行 '启动服务.bat'")
        print("  2. 浏览器访问 http://localhost:5100")
        print("  3. 开始使用结算审核智能体")
    else:
        print("⚠ 系统验证发现问题，请根据上述提示修复。")
        print()
        print("常见解决方案：")
        print("  问题1：依赖包缺失")
        print("    解决：pip install duckdb pandas flask flask-cors")
        print()
        print("  问题2：文件缺失或损坏")
        print("    解决：检查文件是否完整下载/生成")
        print()
        print("  问题3：磁盘空间不足")
        print("    解决：清理磁盘空间，执行 pip cache purge")

    print("=" * 80)
    print()

    return 0 if all_passed else 1

if __name__ == '__main__':
    sys.exit(main())
